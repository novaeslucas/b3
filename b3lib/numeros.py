"""Números e escalas lineares do score. Sem I/O."""
import html


def num(s):
    """'1.234,5%' -> 12.345 ; '12,5%' -> 0.125 ; '1.234,5' -> 1234.5 ; '-' / '' -> None."""
    s = html.unescape(s or "").strip()
    if s in ("", "-"):
        return None
    pct = s.endswith("%")
    s = s.rstrip("%").replace(".", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v / 100 if pct else v


def lin(x, ruim, bom):
    """Mapeia x linearmente para 0..1 (ruim -> 0, bom -> 1), com saturação. Funciona nos dois sentidos."""
    if x is None:
        return None
    t = (x - ruim) / (bom - ruim)
    return max(0.0, min(1.0, t))


def media(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None
