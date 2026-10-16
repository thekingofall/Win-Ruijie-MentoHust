"""Draw the original app symbol at high resolution, then export Windows sizes."""
from pathlib import Path
from PIL import Image, ImageDraw

assets = Path(__file__).resolve().parents[1] / "src/mentohust_modern/assets"
im = Image.new("RGBA", (512, 512))
d = ImageDraw.Draw(im)
d.rounded_rectangle((12, 12, 500, 500), radius=100, fill="#397EBB")
d.line([(135, 316), (224, 316), (293, 196), (377, 196)], fill="white", width=29, joint="curve")
d.ellipse((88, 270, 180, 362), fill="white")
d.ellipse((332, 150, 424, 242), fill="white")
d.ellipse((338, 340, 412, 414), fill="#53D08C")
im.resize((256, 256), Image.Resampling.LANCZOS).save(assets / "app-icon.png")
im.save(assets / "app-icon.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
