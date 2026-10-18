"""
Convert an Aseprite (.aseprite / .ase) file into a PNG sprite sheet, frames laid out left to right.
Handles RGBA, grayscale and indexed color, all visible layers, raw and compressed cels.
Spec: https://github.com/aseprite/aseprite/blob/main/docs/ase-file-specs.md

Usage:
  .venv/bin/python aseprite_to_png.py bunny.aseprite output/sprites/bunny.png
"""
import struct
import sys
import zlib

from PIL import Image


def read_ase(path):
    data = open(path, "rb").read()
    (_, magic, nframes, w, h, depth, _flags, _speed, _, _, transparent_index,
     _, _ncolors) = struct.unpack_from("<IHHHHHIHIIB3sH", data, 0)
    if magic != 0xA5E0:
        raise ValueError("not an Aseprite file")
    palette = [(0, 0, 0, 0)] * 256
    layers = []           # (visible, opacity, blend) per layer index
    frames = []
    pos = 128
    for _ in range(nframes):
        fsize, fmagic, old_chunks, _duration, _, new_chunks = struct.unpack_from("<IHHH2sI", data, pos)
        if fmagic != 0xF1FA:
            raise ValueError("bad frame header")
        nchunks = new_chunks or old_chunks
        cels = []
        cpos = pos + 16
        for _ in range(nchunks):
            csize, ctype = struct.unpack_from("<IH", data, cpos)
            body = cpos + 6
            if ctype == 0x2004:   # layer
                flags, _ltype, _child, _, _, _blend, opacity = struct.unpack_from("<HHHHHHB", data, body)
                layers.append((bool(flags & 1), opacity))
            elif ctype == 0x2019:  # palette
                size, first, last = struct.unpack_from("<III", data, body)
                p = body + 20
                for i in range(first, last + 1):
                    eflags, r, g, b, a = struct.unpack_from("<HBBBB", data, p)
                    p += 6
                    if eflags & 1:     # has a name string
                        (nlen,) = struct.unpack_from("<H", data, p)
                        p += 2 + nlen
                    palette[i] = (r, g, b, a)
            elif ctype == 0x2005:  # cel
                layer, x, y, opacity, ceL_type = struct.unpack_from("<HhhBH", data, body)
                p = body + 16
                if ceL_type in (0, 2):
                    cw, ch = struct.unpack_from("<HH", data, p)
                    raw = data[p + 4: cpos + csize]
                    if ceL_type == 2:
                        raw = zlib.decompress(raw)
                    cels.append((layer, x, y, opacity, cw, ch, raw))
            cpos += csize
        frames.append(cels)
        pos += fsize

    bpp = {32: 4, 16: 2, 8: 1}[depth]
    out = []
    for cels in frames:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for layer, x, y, opacity, cw, ch, raw in sorted(cels, key=lambda c: c[0]):
            visible, lop = layers[layer] if layer < len(layers) else (True, 255)
            if not visible:
                continue
            px = []
            for i in range(cw * ch):
                if bpp == 4:
                    px.append(tuple(raw[i * 4: i * 4 + 4]))
                elif bpp == 2:
                    v, a = raw[i * 2], raw[i * 2 + 1]
                    px.append((v, v, v, a))
                else:
                    idx = raw[i]
                    px.append((0, 0, 0, 0) if idx == transparent_index else palette[idx])
            cel = Image.new("RGBA", (cw, ch))
            cel.putdata(px)
            alpha = opacity * lop // 255
            if alpha < 255:
                cel.putalpha(cel.getchannel("A").point(lambda a: a * alpha // 255))
            img.alpha_composite(cel, (x, y))
        out.append(img)
    return out


def main():
    src, dst = sys.argv[1], sys.argv[2]
    frames = read_ase(src)
    w, h = frames[0].size
    sheet = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        sheet.paste(f, (i * w, 0))
    sheet.save(dst)
    print(f"{len(frames)} frame(s), {w}x{h} px -> {dst}")


if __name__ == "__main__":
    main()
