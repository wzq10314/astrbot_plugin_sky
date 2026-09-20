"""Local reports: no browser service, HTML execution, or remote font dependency."""
import io
from pathlib import Path


def cards(title, text):
    from PIL import Image, ImageDraw, ImageFont
    font_path = Path(__file__).parent / 'assets' / 'report.ttf'
    regular = ImageFont.truetype(str(font_path), 26)
    heading = ImageFont.truetype(str(font_path), 38)
    probe = ImageDraw.Draw(Image.new('RGB', (1,1)))
    lines = []
    for line in str(text).splitlines():
        current = ''
        for char in line:
            if probe.textlength(current + char, font=regular) > 900:
                lines.append(current)
                current = ''
            current += char
        lines.append(current)
    lines = lines or ['暂无数据']
    results = []
    for start in range(0, len(lines), 42):
        page = lines[start:start+42]
        image = Image.new('RGB', (1000, 170 + 39 * len(page)), '#f1f5fa')
        draw = ImageDraw.Draw(image)
        draw.rectangle((0,0,1000,100), fill='#24465a')
        draw.text((42,28), str(title)[:22], font=heading, fill='white')
        for index, line in enumerate(page):
            draw.text((44,122+39*index), line, font=regular, fill='#233747')
        draw.text((44,image.height-40), f'Tlon-Sky · AstrBot  |  {start//42+1}/{(len(lines)+41)//42}',
                  font=ImageFont.truetype(str(font_path),18), fill='#657c88')
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        results.append(stream.getvalue())
    return results
