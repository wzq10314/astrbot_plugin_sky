"""Local cloudglass reports, rendered with Pillow and the bundled font."""
import io
import math
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

INK = '#f6f0e5'
MUTED = '#b8cad1'
GOLD = '#e9c994'


@lru_cache(maxsize=24)
def font(size):
    return ImageFont.truetype(str(Path(__file__).parent / 'assets' / 'report.ttf'), size)


def text(draw, xy, value, size=26, color=INK):
    draw.text(xy, str(value), font=font(size), fill=color)


def wrap(value, size, width):
    result = []
    for line in str(value).splitlines():
        current = ''
        for char in line:
            if current and font(size).getlength(current + char) > width:
                result.append(current)
                current = ''
            current += char
        result.append(current)
    return result or ['']


@lru_cache(maxsize=4)
def backdrop(height):
    w, h = 250, height // 4
    image = Image.new('RGB', (w, h))
    pixels = image.load()
    for y in range(h):
        t = y / (h - 1)
        for x in range(w):
            glow = math.exp(-(((x / w - .88) / .52) ** 2 + ((t - .30) / .29) ** 2))
            pixels[x, y] = tuple(int(a + (b-a)*t + g*glow) for a,b,g in
                                [(17,74,65), (34,85,43), (53,103,24)])
    draw = ImageDraw.Draw(image, 'RGBA')
    rng = random.Random(108)
    for layer in range(4):
        ridge = [(x, int(h*(.40+layer*.13) + math.sin(x/34+layer)*23 + rng.randrange(-6,7)))
                 for x in range(-10,w+11,5)]
        draw.polygon(ridge + [(w,h),(0,h)], fill=(135+layer*14,164+layer*9,177+layer*8,70))
    for _ in range(55):
        x, y = rng.randrange(-80,w), rng.randrange(int(h*.53),h)
        draw.ellipse((x,y,x+rng.randrange(50,140),y+rng.randrange(8,32)), fill=(204,217,220,rng.randrange(8,28)))
    image = image.resize((1000,height), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(15)).convert('RGBA')
    stars = Image.new('RGBA', image.size)
    draw = ImageDraw.Draw(stars)
    for _ in range(65):
        x,y = rng.randrange(30,970),rng.randrange(20,int(height*.70))
        draw.ellipse((x,y,x+2,y+2),fill=(245,230,199,rng.randrange(40,140)))
    for x,y in [(880,94),(940,193),(41,height-180)]:
        draw.line((x-5,y,x+5,y),fill=(244,222,183,150))
        draw.line((x,y-7,x,y+7),fill=(244,222,183,150))
    return Image.alpha_composite(image,stars)


def panel(image, box, radius=28, fill=(13,32,47,150)):
    layer = Image.new('RGBA',image.size)
    ImageDraw.Draw(layer).rounded_rectangle(box,radius=radius,fill=fill,outline=(225,233,229,42),width=1)
    image.alpha_composite(layer)


def header(image, title, subtitle, page=1):
    draw = ImageDraw.Draw(image)
    draw.line((53,53,83,53), fill=GOLD,width=2)
    text(draw,(97,39),'SKY  /  CHILDREN OF THE LIGHT',17,GOLD)
    title_lines = wrap(title,48,880)
    for i,line in enumerate(title_lines):
        text(draw,(50,85+i*58),line,48)
    text(draw,(54,156+(len(title_lines)-1)*58),subtitle,20,MUTED)
    return (len(title_lines)-1)*58


def finish(image,page,total):
    draw = ImageDraw.Draw(image)
    y = image.height-75
    draw.line((54,y,946,y),fill='#819299')
    text(draw,(54,y+20),'TLON SKY  /  ASTRBOT',16,'#d4dfe0')
    text(draw,(853,y+20),f'{page:02d}  /  {total:02d}',16,'#d4dfe0')
    stream = io.BytesIO()
    image.convert('RGB').save(stream,format='PNG')
    return stream.getvalue()


def height_card(value):
    fields,notes = {},[]
    for line in str(value).splitlines():
        line = line.strip()
        if not line:
            continue
        if '：' in line:
            key,val = line.split('：',1)
            if val.strip():
                if key in fields:
                    return None
                fields[key] = val.strip()
        else:
            notes.append(line)
    keys = ['当前身高','最高可达','最低可达','体型值','身高值','当前身高描述']
    if not all(k in fields for k in keys[:5]):
        return None
    outfits = [(k,v) for k,v in fields.items() if k not in keys]
    if len(outfits)>10 or any(len(k)>8 or len(v)>26 for k,v in outfits):
        return None
    if any(len(fields[k])>15 for k in keys[:5]) or len(fields.get(keys[5],''))>12:
        return None
    note_lines = wrap('\n'.join(notes),21,822) if notes else []
    if len(note_lines) > 8:
        return None
    rows = math.ceil(len(outfits)/2)
    height = max(1230,817+rows*105+len(note_lines)*32+140)
    image = backdrop(height).copy()
    header(image,'光遇身高','身高测量  ·  角色装扮')
    panel(image,(48,213,952,521),fill=(15,34,48,154))
    draw = ImageDraw.Draw(image)
    text(draw,(80,245),'当前身高',24,MUTED)
    number_size = 100
    while number_size > 36 and font(number_size).getlength(fields['当前身高']) > 575:
        number_size -= 2
    text(draw,(75,285),fields['当前身高'],number_size)
    description = fields.get('当前身高描述','')
    if description:
        tag_width = int(font(21).getlength(description))+34
        panel(image,(81,438,81+tag_width,480),radius=14,fill=(174,141,87,50))
        text(ImageDraw.Draw(image),(98,447),description,21,GOLD)
    art = Image.new('RGBA',image.size)
    a = ImageDraw.Draw(art)
    a.arc((706,260,870,452),180,360,fill=(234,210,164,215),width=3)
    a.line((706,357,706,459),fill=(234,210,164,190),width=3)
    a.line((870,357,870,459),fill=(234,210,164,190),width=3)
    a.arc((721,276,855,439),180,360,fill=(234,210,164,70),width=1)
    a.line((721,357,721,459),fill=(234,210,164,70))
    a.line((855,357,855,459),fill=(234,210,164,70))
    a.ellipse((779,356,797,374),fill=(255,240,199,240))
    a.polygon([(788,378),(759,422),(788,437),(817,422)],fill=(244,219,175,215))
    a.line((788,436,782,459),fill=(247,228,188,210),width=3)
    a.line((788,436,796,459),fill=(247,228,188,210),width=3)
    a.ellipse((728,462,849,466),fill=(236,213,168,125))
    image.alpha_composite(art.filter(ImageFilter.GaussianBlur(17)))
    image.alpha_composite(art)
    for i,key in enumerate(keys[1:5]):
        x = 48+i*231
        panel(image,(x,542,x+211,680),radius=23)
        draw = ImageDraw.Draw(image)
        text(draw,(x+21,563),key,20,MUTED)
        metric_size = 30
        while metric_size > 14 and font(metric_size).getlength(fields[key]) > 174:
            metric_size -= 1
        text(draw,(x+20,607),fields[key],metric_size)
    panel(image,(48,702,952,height-104),fill=(16,36,49,169))
    draw = ImageDraw.Draw(image)
    text(draw,(80,733),'当前角色装扮',29)
    text(draw,(758,743),'WARDROBE',15,GOLD)
    for i,(key,val) in enumerate(outfits):
        x,y = 80+(i%2)*443,814+(i//2)*105
        draw.line((x,y-10,x+367,y-10),fill='#51636f')
        text(draw,(x,y),key,19,MUTED)
        for j,line in enumerate(wrap(val,25,367)):
            text(draw,(x,y+32+j*31),line,25)
    for i,line in enumerate(note_lines):
        text(draw,(80,824+rows*105+i*32),line,21,MUTED)
    return finish(image,1,1)


def cards(title, value):
    if str(title)=='光遇身高':
        result = height_card(value)
        if result is not None:
            return [result]
    lines = wrap(value,27,820)
    extra = (len(wrap(title,48,880))-1)*58
    total = math.ceil(len(lines)/31)
    results = []
    for start in range(0,len(lines),31):
        page = lines[start:start+31]
        image = backdrop(max(590,342+len(page)*44+extra)).copy()
        header(image,str(title),'光遇旅途  ·  查询报告')
        panel(image,(48,216+extra,952,image.height-97))
        draw = ImageDraw.Draw(image)
        for i,line in enumerate(page):
            text(draw,(82,244+extra+i*44),line,27)
        results.append(finish(image,start//31+1,total))
    return results
