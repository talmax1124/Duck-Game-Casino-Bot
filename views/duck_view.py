from PIL import Image, ImageDraw, ImageFont


def generate_duck_game_image(position, hazard, duck_path, car_path, road_path, grass_path, bank=0, multiplier=1, save=False, game_started=False):
    tile_width = 150
    tile_height = 200
    steps = 6
    total_width = tile_width * steps
    total_height = tile_height + 100  # Further increased for full canvas visibility

    background = Image.new("RGBA", (total_width, total_height), (0, 0, 0, 255))

    try:
        road_tile = Image.open(road_path).resize((tile_width, tile_height))
    except Exception as e:
        raise
    try:
        grass_tile = Image.open(grass_path).resize((tile_width, tile_height))
    except Exception as e:
        raise
    try:
        duck = Image.open(duck_path).convert("RGBA").resize((tile_width, tile_height))
    except Exception as e:
        raise
    try:
        car = Image.open(car_path).convert("RGBA").resize((tile_width, tile_height))
    except Exception as e:
        raise

    if not game_started:
        for i in range(steps + 1):
            background.paste(grass_tile, (i * tile_width, 0))
    else:
        for i in range(steps):
            background.paste(road_tile, (i * tile_width, 0))
        background.paste(grass_tile, (0, 0))

    if hazard >= 0 and hazard < steps and position == hazard:
        background.paste(car, (hazard * tile_width, 0), car)


    if position == -1:
        background.paste(grass_tile, (0, 0))
        background.paste(duck, (0, 0), duck)
    elif 0 <= position < steps:
        background.paste(duck, (position * tile_width, 0), duck)

    draw = ImageDraw.Draw(background)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()

    draw.rectangle([(5, tile_height + 20), (250, tile_height + 70)], fill=(0, 0, 0, 180))
    draw.text((10, tile_height + 25), f"Bank: ${bank}", font=font, fill=(255, 255, 255, 255))
    draw.text((10, tile_height + 45), f"Multiplier: x{multiplier}", font=font, fill=(255, 255, 255, 255))

    if save:
        output_path = f"assets/generated/duck_game_pos{position}_haz{hazard}.png"
        background.save(output_path)
        return output_path
    else:
        return background