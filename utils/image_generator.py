import logging
from PIL import Image

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def generate_duck_game_image(position: int, hazard_pos: int, previous_positions: list[int]) -> Image.Image:
    logger.debug(f"Generating image with position={position}, hazard_pos={hazard_pos}, previous_positions={previous_positions}")
    # Load base road image
    road = Image.open("assets/road/road.png").convert("RGBA")
    grass = Image.open("assets/road/Grass.png").convert("RGBA")
    duck = Image.open("assets/duck_images/duck.png").convert("RGBA")
    car = Image.open("assets/road/car.png").convert("RGBA")
    car = car.rotate(270, expand=True)
    car = car.resize((car.width // 8, car.height // 8))
    logger.debug("Loaded road, grass, duck, and car images.")

    # Constants
    tile_width = road.width
    tile_height = road.height
    total_slots = 5

    # Create canvas (1 grass tile + 5 road tiles + 1 extra tile) horizontally, height is max of all elements increased by 10%
    canvas = Image.new(
        "RGBA",
        (
            tile_width * (total_slots + 2),
            int(max(grass.height, road.height, duck.height, car.height) * 1.1)
        )
    )
    logger.debug(f"Created canvas of size: {canvas.size}")

    # Add grass at the left, aligned to bottom
    canvas.paste(grass, (0, canvas.height - grass.height))

    # Add road tiles horizontally, aligned to bottom
    for i in range(total_slots):
        logger.debug(f"Placing road tile at x={(i + 1) * tile_width}")
        canvas.paste(road, (tile_width * (i + 1), canvas.height - road.height))

    # Add duck horizontally, aligned to bottom; if position == -1, place on grass
    if position == -1:
        duck_x = 0
    elif position <= total_slots:
        duck_x = tile_width * (position + 1)
    else:
        duck_x = tile_width * (total_slots + 1)
    duck_y = canvas.height - duck.height
    logger.debug(f"Placing duck at x={duck_x}, y={duck_y}")
    canvas.paste(duck, (duck_x, duck_y), duck)

    # Add car at hazard position if applicable, aligned to bottom
    if 0 <= hazard_pos < total_slots:
        car_x = tile_width * (hazard_pos + 1)
        car_y = canvas.height - car.height
        logger.debug(f"Placing car at x={car_x}, y={car_y}")
        canvas.paste(car, (car_x, car_y), car)

    return canvas