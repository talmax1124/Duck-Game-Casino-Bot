import logging
from PIL import Image
from typing import List

def generate_duck_game_image(position: int, hazard_pos: int, previous_positions: List[int]) -> Image.Image:
    """
    Generate a horizontal scene: [GRASS] [LANE0] [LANE1] [LANE2] [LANE3] [LANE4] [EXTRA PAD]
    - position: -1 for grass start, 0..4 for lane index, >=5 means passed final lane (draw on extra pad)
    - hazard_pos: 0..4 for the lane containing the car hazard; outside that range -> no car
    - previous_positions: unused here (kept for signature compatibility)
    """
    # Load images
    road = Image.open("assets/road/road.png").convert("RGBA")
    grass = Image.open("assets/road/Grass.png").convert("RGBA")
    duck = Image.open("assets/duck_images/duck.png").convert("RGBA")
    car = Image.open("assets/road/car.png").convert("RGBA")
    # Rotate car to face "across" the lanes and scale down
    car = car.rotate(270, expand=True)
    car = car.resize((car.width // 8, car.height // 8))

    # Constants
    total_slots = 5  # number of road lanes
    lane_width = road.width          # width of each road tile
    lane_height = road.height
    grass_width = grass.width        # actual width of the grass column
    grass_height = grass.height
    extra_pad_width = lane_width     # extra space to show duck beyond last lane

    # Canvas size: grass + all lanes + one extra pad
    canvas_width = grass_width + (total_slots * lane_width) + extra_pad_width
    canvas_height = int(max(grass_height, lane_height, duck.height, car.height) * 1.1)
    canvas = Image.new("RGBA", (canvas_width, canvas_height))

    # Paste grass at the left, bottom-aligned
    canvas.paste(grass, (0, canvas_height - grass_height))

    # Paste lanes horizontally after the grass, bottom-aligned
    for i in range(total_slots):
        x = grass_width + (i * lane_width)
        canvas.paste(road, (x, canvas_height - lane_height))

    # Paste one extra road tile in the extra pad area, bottom-aligned
    extra_x = grass_width + (total_slots * lane_width)
    canvas.paste(road, (extra_x, canvas_height - lane_height))

    # Helper: center X for a given lane index (-1 = grass, 0..4 = lanes, 5 = extra pad)
    def center_x_for_index(idx: int) -> int:
        if idx == -1:
            # Grass column center
            return grass_width // 2
        if 0 <= idx < total_slots:
            left = grass_width + (idx * lane_width)
            return left + (lane_width // 2)
        # Beyond final lane -> extra pad center
        extra_left = grass_width + (total_slots * lane_width)
        return extra_left + (extra_pad_width // 2)

    # ---- Place DUCK, centered in its lane (or grass) ----
    # Determine the logical index to use for centering
    if position <= -1:
        duck_idx = -1
    elif 0 <= position < total_slots:
        duck_idx = position
    else:
        duck_idx = total_slots  # beyond last lane -> extra pad

    duck_center_x = center_x_for_index(duck_idx)
    duck_x = int(duck_center_x - duck.width / 2)
    duck_y = canvas_height - duck.height
    canvas.paste(duck, (duck_x, duck_y), duck)

    # ---- Place CAR (hazard), centered in its lane, only if hazard_pos is a valid lane ----
    if 0 <= hazard_pos < total_slots:
        car_center_x = center_x_for_index(hazard_pos)
        car_x = int(car_center_x - car.width / 2)
        car_y = canvas_height - car.height
        canvas.paste(car, (car_x, car_y), car)

    return canvas