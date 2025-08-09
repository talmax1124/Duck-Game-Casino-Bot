import os
import io
from typing import Iterable, Tuple, Optional

import discord
from PIL import Image, ImageDraw
from discord.ext import commands

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
IMAGE_CDN_CHANNEL_ID = int(os.getenv("IMAGE_CDN_CHANNEL_ID", "0"))

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
ROAD_DIR = os.path.join(ASSETS_DIR, "road")
DUCK_DIR = os.path.join(ASSETS_DIR, "duck_images")

GRASS_PATH = os.path.join(ROAD_DIR, "Grass.png")
LANE_PATH = os.path.join(ROAD_DIR, "road.png")
END_PATH = os.path.join(ROAD_DIR, "end.png")  # finish lane image
CAR_PATH = os.path.join(ROAD_DIR, "car.png")
DUCK_PATH = os.path.join(DUCK_DIR, "duck.png")

# Fallback sizes in case assets are missing
DEFAULT_TILE_W, DEFAULT_TILE_H = 256, 256

# ---------------------------------------------------------------------------
# Image CDN helper (uploads a PIL image to a private channel and returns URL)
# ---------------------------------------------------------------------------
async def upload_frame_and_get_url(
    bot: commands.Bot,
    pil_img: Image.Image,
    filename: str,
    delete_last_id: Optional[int] = None,
) -> Tuple[str, int]:
    """Upload a PIL image to the configured CDN channel and return (url, msg_id).

    Requires IMAGE_CDN_CHANNEL_ID and the bot having Send/Attach/Delete in that channel.
    """
    if not IMAGE_CDN_CHANNEL_ID:
        raise RuntimeError("IMAGE_CDN_CHANNEL_ID not set in environment")

    chan = bot.get_channel(IMAGE_CDN_CHANNEL_ID)
    if chan is None:
        chan = await bot.fetch_channel(IMAGE_CDN_CHANNEL_ID)

    if delete_last_id:
        try:
            old = await chan.fetch_message(delete_last_id)
            await old.delete()
        except Exception:
            pass

    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    buf.seek(0)

    cdn_msg = await chan.send(file=discord.File(buf, filename=filename))
    url = cdn_msg.attachments[0].url
    return url, cdn_msg.id

# ---------------------------------------------------------------------------
# Board rendering
# ---------------------------------------------------------------------------

def _load_image(path: str, fallback_size: Tuple[int, int] = (DEFAULT_TILE_W, DEFAULT_TILE_H)) -> Image.Image:
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        # Solid gray fallback so the game still runs without assets
        return Image.new("RGBA", fallback_size, (120, 120, 120, 255))


def _center_paste(base: Image.Image, overlay: Image.Image, box: Tuple[int, int, int, int]):
    """Paste overlay centered inside the given (left, top, right, bottom) box."""
    l, t, r, b = box
    box_w, box_h = r - l, b - t
    ow, oh = overlay.size
    x = l + (box_w - ow) // 2
    y = t + (box_h - oh) // 2
    base.alpha_composite(overlay, (x, y))


def generate_duck_game_image(
    position: int,
    hazard_pos: int,
    previous_positions: Iterable[int],
    total_slots: int,
) -> Image.Image:
    """Render the board as a single image.

    Columns (left to right):
      - grass (start, virtual index -1)
      - regular lanes 0..total_slots-1
      - finish lane at index == total_slots

    Rules:
      - Show the car only when a crash is being displayed (i.e., caller passes
        hazard_pos equal to current position). This matches the requirement
        that the car is invisible until the lane is reached and ends the game.
      - The duck is centered in the current column (grass -> index -1).
    """
    # Load tiles
    grass = _load_image(GRASS_PATH)
    lane = _load_image(LANE_PATH)
    finish = _load_image(END_PATH)
    car = _load_image(CAR_PATH)
    duck = _load_image(DUCK_PATH)

    # Normalize sizes (all tiles to lane tile size)
    tile_w, tile_h = lane.size if lane else (DEFAULT_TILE_W, DEFAULT_TILE_H)
    grass = grass.resize((tile_w, tile_h), Image.LANCZOS)
    finish = finish.resize((tile_w, tile_h), Image.LANCZOS)

    # Scale entities
    # Car: width is slightly bigger than before; keep aspect and cast to ints
    cw = max(1, tile_w / 1.8)
    ch = max(1, int(round(car.height * (cw / float(car.width)))))
    car = car.resize((int(cw), int(ch)), Image.LANCZOS)
    # rotate 270 degrees to match game orientation
    car = car.rotate(270, expand=True)
    # Duck: width is exactly tile_w/2; keep aspect and cast to ints
    dw = max(1, tile_w / 2)
    dh = max(1, int(round(duck.height * (dw / float(duck.width)))))
    duck = duck.resize((int(dw), int(dh)), Image.LANCZOS)

    # Canvas columns: grass + lanes + finish
    columns = 1 + total_slots + 1
    canvas = Image.new("RGBA", (tile_w * columns, tile_h), (0, 0, 0, 0))

    # Compose background
    x = 0
    canvas.alpha_composite(grass, (x, 0))
    x += tile_w
    for _ in range(total_slots):
        canvas.alpha_composite(lane, (x, 0))
        x += tile_w
    canvas.alpha_composite(finish, (x, 0))

    # Compute column rectangles for centering entities
    col_boxes = [
        (i * tile_w, 0, (i + 1) * tile_w, tile_h) for i in range(columns)
    ]

    # Duck column index in the composed image
    duck_col = position + 1  # shift because grass is column 0
    duck_col = max(0, min(duck_col, columns - 1))

    # Draw duck
    _center_paste(canvas, duck, col_boxes[duck_col])

    # Draw car only when hazard_pos is the *current* position (crash frame)
    if hazard_pos >= 0 and position == hazard_pos:
        car_col = hazard_pos + 1
        _center_paste(canvas, car, col_boxes[car_col])

    return canvas

__all__ = [
    "generate_duck_game_image",
    "upload_frame_and_get_url",
]