"""Generate editable SVG artwork and a font-independent identity presentation."""
from pathlib import Path
from html import escape
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[2]
FONT=REPO/'presentation/launch-film-magic/assets/InterTight.ttf'
TEAL='#024950'
AQUA='#0fa4af'
MIST='#afdde5'
INK='#003135'
PAPER='#f5f9fa'
PATHS=['M8 66V14H23L37 30V51L23 35V66Z',
       'M43 30L57 14H72V66H57V35L43 51Z']
font=instantiateVariableFont(TTFont(FONT),{'wght':550})
glyphs=font.getGlyphSet()
cmap=font.getBestCmap()
upm=font['head'].unitsPerEm

def text_paths(text,size,x=0,y=0,color=INK,tracking=-.8):
    scale=size/upm
    position=0
    paths=[]
    for char in text:
        name=cmap[ord(char)]
        pen=SVGPathPen(glyphs)
        glyphs[name].draw(pen)
        paths.append(f'<path d="{pen.getCommands()}" transform="translate({position:.3f} 0)"/>')
        position+=glyphs[name].width+tracking/scale
    return f'<g fill="{color}" transform="translate({x} {y}) scale({scale} {-scale})">'+''.join(paths)+'</g>', (position-tracking/scale)*scale

def mark(color=None, dark=False):
    colors=[color,color] if color else ([MIST,AQUA] if dark else [TEAL,AQUA])
    return ''.join(f'<path fill="{c}" d="{p}"/>' for p,c in zip(PATHS,colors))

def svg(content,w,h,title):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(title)}"><title>{escape(title)}</title>{content}</svg>\n'

def save(name,content,w,h,title):
    (ROOT/name).write_text(svg(content,w,h,title))

for suffix,color in [('',None),('-ink',INK),('-white','#ffffff')]:
    save(f'memoryworks-mark{suffix}.svg',mark(color),80,80,'MemoryWorks symbol')

for suffix,color in [('',INK),('-dark',PAPER)]:
    word,width=text_paths('memoryworks',78,112,88,color,-1.5)
    save(f'memoryworks-lockup{suffix}.svg',
        '<g transform="translate(0 10) scale(1.2)">'+mark(dark=bool(suffix))+'</g>'+word,
        round(112+width+12),120,'MemoryWorks')

tile=f'<rect width="80" height="80" rx="18" fill="{INK}"/>'
tile+='<g transform="translate(6.4 6.4) scale(.84)">'+mark(dark=True)+'</g>'
save('memoryworks-icon.svg',tile,80,80,'MemoryWorks app icon')

# Presentation deliberately contains the exact production geometry.
board=[f'<rect width="1600" height="1200" fill="{PAPER}"/>']
def txt(text,size,x,y,color=INK,tracking=0):
    p,w=text_paths(text,size,x,y,color,tracking)
    board.append(p)
    return w
txt('MEMORYWORKS',18,80,76,INK,2.4)
txt('IDENTITY / 01',14,1325,76,'#62797c',1.2)
board.append('<path d="M80 105H1520" stroke="#dde9eb"/>')
board.append('<g transform="translate(168 225) scale(2.3)">'+mark()+'</g>')
txt('memoryworks',118,393,359,INK,-2.7)
txt('memoryworks.app',28,397,421,'#62797c',.2)
txt('Context, put to work.',34,170,545,INK,-.4)
board.append(f'<rect x="0" y="635" width="1600" height="565" fill="{INK}"/>')
txt('THE MARK',13,80,691,MIST,1.8)
txt('DARK / MONOCHROME',13,654,691,MIST,1.8)
txt('AT EVERY SIZE',13,1160,691,MIST,1.8)
board.append('<path d="M580 675V1100M1095 675V1100" stroke="#356267"/>')
board.append('<g transform="translate(145 732) scale(3.5)">'+mark(dark=True)+'</g>')
board.append('<g transform="translate(654 791) scale(1.2)">'+mark(PAPER)+'</g>')
txt('memoryworks',49,770,865,PAPER,-1)
board.append('<g transform="translate(655 966) scale(.72)">'+mark(PAPER)+'</g>')
txt('memoryworks.app',24,732,1004,MIST,.1)
for i,size in enumerate([16,24,32,64]):
    x=1160+i*84
    board.append(f'<g transform="translate({x} {829-size/2}) scale({size/80})">{mark(PAPER)}</g>')
    txt(str(size),13,x,901,MIST,.3)
board.append('<g transform="translate(1154 956) scale(1.2)">'+tile+'</g>')
txt('App icon',18,1275,1004,PAPER)
txt('Two folds. One shared memory.',20,80,1150,MIST,.1)
txt('2026',14,1460,1150,MIST,1)
save('memoryworks-brand-board.svg',''.join(board),1600,1200,'MemoryWorks identity: primary logo, dark logo, monochrome and icon sizes')
print('SVG identity generated:',ROOT)
