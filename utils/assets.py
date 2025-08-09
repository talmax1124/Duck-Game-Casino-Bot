from PIL import Image, ImageDraw, ImageFont
import os
import discord
import io
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "..", "assets")
ROAD_DIR = os.path.join(ASSETS_DIR, "road")
DUCK_DIR = os.path.join(ASSETS_DIR, "duck_images")
OUTPUT_DIR = os.path.join(ASSETS_DIR, "output")

# Simple image cache to avoid reloading from disk repeatedly
_IMG_CACHE = {}

def _load_img(path: str) -> Image.Image:
    abspath = os.path.abspath(path)
    if abspath in _IMG_CACHE:
        return _IMG_CACHE[abspath]
    img = Image.open(abspath).convert("RGBA")
    _IMG_CACHE[abspath] = img
    return img


def get_duck_visual(
    position: int,
    max_length: int = 5,
    *,
    hazard_pos: int = -1,
    show_hazard: bool = True,
    labels: Optional[List[str]] = None,
    base_width: int = 100,
    duck_scale: float = 1.0,
    car_scale: float = 0.5,
    filename: str = "duck_game_status.gif",
) -> Optional[discord.File]:
    """
    Create an animated GIF of the duck moving horizontally across lanes with an optional hazard car.

    Args:
        position: The last lane index the duck has reached. Use -1 to show duck in the grass only.
        max_length: Number of playable lanes (excludes grass; includes finish as last tile when drawn).
        hazard_pos: Lane index (1..max_length) where the car is. -1 to hide the car entirely.
        show_hazard: If True, the car appears only when the duck reaches that hazard lane; otherwise hidden.
        labels: Optional list of multiplier labels for each playable lane (length should match max_length).
        base_width: Width of each tile (each lane & grass). Height will be derived from assets.
        duck_scale: Relative scale of duck sprite (1.0 ≈ tile height).
        car_scale: Relative scale of car sprite.
        filename: Output GIF filename.

    Returns:
        discord.File of the generated GIF, or None on failure.
    """

    # Load tiles/sprites
    grass_path = os.path.join(ROAD_DIR, "Grass.png")
    road_path = os.path.join(ROAD_DIR, "road.png")
    finish_path = os.path.join(ROAD_DIR, "end.png")
    duck_path = os.path.join(DUCK_DIR, "duck.png")
    car_path = os.path.join(ROAD_DIR, "car.png")

    try:
        grass = _load_img(grass_path)
        road = _load_img(road_path)
        duck = _load_img(duck_path)
        car = _load_img(car_path)
    except FileNotFoundError:
        return None

    # Optional finish tile; fall back to road if missing
    finish = _load_img(finish_path) if os.path.exists(finish_path) else road

    # Normalize base tile height from road, fix sizes
    lane_h = int(base_width * (road.height / max(1, road.width)))
    lane_w = base_width

    # Resize tiles to our target lane size
    road = road.resize((lane_w, lane_h), Image.LANCZOS)
    grass = grass.resize((lane_w, lane_h), Image.LANCZOS)
    finish = finish.resize((lane_w, lane_h), Image.LANCZOS)

    # Resize sprites relative to lane height
    d_h = int(lane_h * duck_scale)
    d_w = max(1, int(duck.width * (d_h / max(1, duck.height))))
    duck_img = duck.resize((d_w, d_h), Image.LANCZOS)

    c_h = int(lane_h * car_scale)
    c_w = max(1, int(car.width * (c_h / max(1, car.height))))
    car_img = car.resize((c_w, c_h), Image.LANCZOS).rotate(90, expand=True)

    # Canvas sizing: grass + lanes + finish; give vertical margin for duck overlap
    cols = 1 + max_length + 1  # grass + playable lanes + finish tile
    image_width = lane_w * cols
    image_height = lane_h + d_h  # room below for duck to sit visually "on" the lanes

    # Labels
    if labels is None:
        labels = []
        # Default labels like x1.2, x1.5 ... if desired length mismatch, we'll just draw what we have
        defaults = ["x1.20", "x1.50", "x1.70", "x2.00", "x2.40"]
        for i in range(max_length):
            labels.append(defaults[i] if i < len(defaults) else f"x1.{i}")

    # Try find a reasonable font, fallback to default
    font = None
    for fp in [
        "/System/Library/Fonts/Supplemental/Arial.ttf",  # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux common
    ]:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 18)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()

    # Helper: center x for a given column index
    # col 0 = grass, 1..max_length = lanes, max_length+1 = finish
    def col_center_x(col_index: int) -> int:
        left = col_index * lane_w
        return left + lane_w // 2

    # Determine how many frames (0..position). Position -1 => just grass frame
    frames = []
    last_step = max(-1, min(position, max_length + 1))
    for step in range(-1, last_step + 1):
        # Create background
        background = Image.new("RGBA", (image_width, image_height), (30, 30, 30, 255))
        draw = ImageDraw.Draw(background)

        # Paste grass
        background.paste(grass, (0, 0), grass)

        # Paste playable lanes
        for i in range(1, max_length + 1):
            x = i * lane_w
            background.paste(road, (x, 0), road)

        # Paste finish lane at the end
        finish_col = max_length + 1
        background.paste(finish, (finish_col * lane_w, 0), finish)

        # Draw lane separators (optional; keep subtle)
        for i in range(1, max_length + 1):
            x = i * lane_w
            draw.line([(x, 0), (x, lane_h)], fill=(255, 255, 255, 120), width=2)

        # Draw labels above lanes (1..max_length)
        for i in range(1, max_length + 1):
            label = labels[i - 1] if i - 1 < len(labels) else ""
            if label:
                tx = i * lane_w + 8
                ty = 6
                draw.text((tx, ty), label, fill=(255, 255, 255, 220), font=font)

        # Duck lane (convert step to a column): -1 => grass(0), 0..max_length-1 => 1..max_length, >=max_length => finish
        if step <= -1:
            duck_col = 0
        elif step >= max_length:
            duck_col = max_length + 1  # finish
        else:
            duck_col = step + 1

        # Place duck bottom-aligned and centered in its column
        dx_center = col_center_x(duck_col)
        dx = int(dx_center - duck_img.width / 2)
        dy = lane_h  # sit on bottom area
        background.paste(duck_img, (dx, dy - duck_img.height), duck_img)

        # Show hazard car only when duck has reached its lane and show_hazard is True
        if show_hazard and 1 <= hazard_pos <= max_length and step == (hazard_pos - 1):
            cx_center = col_center_x(hazard_pos)
            cx = int(cx_center - car_img.width / 2)
            cy = int((lane_h - car_img.height) / 2)
            background.paste(car_img, (cx, cy), car_img)

        frames.append(background)

    # Save animated GIF
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, filename)
    if not frames:
        return None

    # Optimize first frame size and append others
    frames[0].save(output_path, save_all=True, append_images=frames[1:], duration=300, loop=0)

    if os.path.exists(output_path):
        return discord.File(output_path, filename=filename)
    return None