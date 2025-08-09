from PIL import Image
from typing import List
import os

# Simple cache to avoid reloading assets repeatedly
_IMG_CACHE = {}

def _load_img(path: str) -> Image.Image:
    if path in _IMG_CACHE:
        return _IMG_CACHE[path]
    img = Image.open(path).convert("RGBA")
    _IMG_CACHE[path] = img
    return img


def generate_duck_game_image(
    position: int,
    hazard_pos: int,
    previous_positions: List[int],
    *,
    total_slots: int = 5,
    show_hazard: bool = True,
) -> Image.Image:
    """
    Generate a horizontal scene with variable lanes and a finish tile:
      [GRASS] [LANE 0] ... [LANE N-1] [FINISH]

    Args:
        position: -1 for grass start, 0..total_slots-1 for lane index,
                  >= total_slots means draw on the finish lane.
        hazard_pos: 0..total_slots-1 for the lane containing the car hazard; any other value -> no car.
        previous_positions: kept for signature compatibility (unused here).
        total_slots: number of playable road lanes (excludes grass and finish lane).
        show_hazard: if False, never draw the car; if True, draw car only when hazard_pos is within 0..total_slots-1.
    """
    # Load assets
    road = _load_img("assets/road/road.png")
    grass = _load_img("assets/road/Grass.png")
    duck = _load_img("assets/duck_images/duck.png")
    car = _load_img("assets/road/car.png")
    finish = _load_img("assets/road/end.png") if os.path.exists("assets/road/end.png") else road

    # Normalize sizes based on road tile
    lane_w, lane_h = road.width, road.height

    # Resize grass/finish to lane box
    if grass.height != lane_h:
        grass = grass.resize((grass.width, lane_h))
    if finish.size != (lane_w, lane_h):
        finish = finish.resize((lane_w, lane_h))

    # Canvas size: grass + N lanes + finish + small padding
    right_pad = int(lane_w * 0.15)
    canvas_w = grass.width + total_slots * lane_w + finish.width + right_pad
    canvas_h = max(lane_h, duck.height)
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (18, 18, 18, 255))

    # Paste grass at left, bottom-aligned
    canvas.paste(grass, (0, canvas_h - grass.height), grass)

    # Paste lanes after grass
    base_x = grass.width
    for i in range(total_slots):
        x = base_x + i * lane_w
        canvas.paste(road, (x, canvas_h - lane_h), road)

    # Paste finish lane
    finish_x = base_x + total_slots * lane_w
    canvas.paste(finish, (finish_x, canvas_h - lane_h), finish)

    # Helper: center X for a given logical index
    # -1 = grass, 0..total_slots-1 = lanes, total_slots or more = finish
    def center_x_for_index(idx: int) -> int:
        if idx < 0:
            return grass.width // 2
        if 0 <= idx < total_slots:
            left = base_x + idx * lane_w
            return left + lane_w // 2
        return finish_x + lane_w // 2

    # Scale sprites relative to lane height (smaller: 1/8 of lane height)
    duck_h = int(lane_h / 8)
    duck_w = max(1, int(duck.width * (duck_h / max(1, duck.height))))
    duck_img = duck.resize((duck_w, duck_h), Image.LANCZOS)

    car_h = int(lane_h / 8)
    car_w = max(1, int(car.width * (car_h / max(1, car.height))))
    # Rotate car to face across lanes (90 degrees)
    car_img = car.resize((car_w, car_h), Image.LANCZOS).rotate(270, expand=True)

    # ---- Place CAR (hazard) only if requested and within lanes ----
    if show_hazard and 0 <= hazard_pos < total_slots:
        car_center_x = center_x_for_index(hazard_pos)
        car_x = int(car_center_x - car_img.width / 2)
        car_y = canvas_h - lane_h + (lane_h - car_img.height) // 2
        canvas.paste(car_img, (car_x, car_y), car_img)

    # ---- Place DUCK ----
    if position <= -1:
        duck_idx = -1
    elif 0 <= position < total_slots:
        duck_idx = position
    else:
        duck_idx = total_slots  # draw on finish lane

    duck_center_x = center_x_for_index(duck_idx)
    duck_x = int(duck_center_x - duck_img.width / 2)
    duck_y = canvas_h - duck_img.height  # bottom align
    canvas.paste(duck_img, (duck_x, duck_y), duck_img)

    return canvas