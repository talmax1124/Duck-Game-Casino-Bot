from PIL import Image, ImageDraw, ImageFont
import os
import discord
import random
import io

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_duck_visual(position: int, max_length=5) -> discord.File:
    print(f"[DEBUG] Generating duck visual at position: {position}")
    base_width = 100
    base_height = 100
    image_width = base_width * (max_length + 1)
    image_height = base_height + duck.height  # ensures full fit
    frames = []

    try:
        duck_path = os.path.join(BASE_DIR, "..", "assets", "duck_images", "duck.png")
        duck = Image.open(duck_path).convert("RGBA").resize((base_width, base_height))
        print(f"[DEBUG] Loaded duck image from {duck_path}")
    except FileNotFoundError:
        print(f"[ERROR] Could not load duck image at {duck_path}")
        return None

    try:
        car_path = os.path.join(BASE_DIR, "..", "assets", "road", "car.png")
        car = Image.open(car_path).convert("RGBA").resize((int(base_width * 0.5), int(base_height * 0.5))).rotate(90, expand=True)
        print(f"[DEBUG] Loaded car image from {car_path}")
    except FileNotFoundError:
        print(f"[ERROR] Could not load car image at {car_path}")
        return None

    font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    try:
        font = ImageFont.truetype(font_path, 18)
    except IOError:
        font = ImageFont.load_default()

    car_lane = random.randint(1, max_length)

    for step in range(position + 1):
        background = Image.new("RGBA", (image_width, image_height), (30, 30, 30, 255))
        draw = ImageDraw.Draw(background)

        # Draw tiles and lane markers
        for i in range(max_length + 1):
            tile_path = os.path.join(BASE_DIR, "..", "assets", "road", "Grass.png") if i == 0 else os.path.join(BASE_DIR, "..", "assets", "road", "road.png")
            try:
                tile = Image.open(tile_path).convert("RGBA").resize((base_width, base_height))
                background.paste(tile, (i * base_width, 0), tile)
            except FileNotFoundError:
                print(f"[ERROR] Could not load tile at {tile_path}")

        # Draw lane lines and barricades
        for i in range(1, max_length + 1):
            x = i * base_width
            draw.line([(x, 0), (x, base_height)], fill=(255, 255, 255, 255), width=2)
            draw.line([(x - base_width//2, 0), (x - base_width//2, base_height)],
                      fill=(255, 0, 0, 255), width=5)

        # Calculate duck lane before barricade block
        duck_lane = min(step + 1, max_length)

        print(f"[DEBUG] Drawing frame for step {step}, duck lane: {duck_lane}, car lane: {car_lane}")

        # Add lane labels
        labels = ["1.2X", "1.5X", "1.7X", "2.0X", "2.4X"]
        for i, label in enumerate(labels):
            draw.text((base_width * (i + 1) + 10, 5), label, fill="white", font=font)

        # Place duck at bottom center
        duck_x = duck_lane * base_width
        background.paste(duck, (duck_x, base_height), duck)
        print(f"[DEBUG] Pasted duck at lane {duck_lane}")

        # Only show car when duck reaches that lane
        if step == car_lane:
            car_x = car_lane * base_width
            car_offset_x = int((base_width - car.width) / 2)
            car_offset_y = int((base_height - car.height) / 2)
            background.paste(car, (car_x + car_offset_x, car_offset_y), car)
            print(f"[DEBUG] Pasted car at lane {car_lane}")

        frames.append(background)

    output_path = os.path.join(BASE_DIR, "..", "assets", "output", "duck_game_status.gif")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    frames[0].save(output_path, save_all=True, append_images=frames[1:], duration=300, loop=0)
    if os.path.exists(output_path):
        print(f"[DEBUG] Visual GIF saved to {output_path}")
        return discord.File(output_path, filename="duck_game_status.gif")
    else:
        print("[ERROR] Failed to create output GIF")
        return None