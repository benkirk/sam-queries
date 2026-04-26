#!/usr/bin/env python3
"""Re-embed Poppins into a pandoc-rendered pptx.

Reads the four EOT-subsetted Poppins blobs from assets/fonts/ (produced once by
prepare_template.py) and injects them into the given .pptx:

  * adds ppt/fonts/poppins-{regular,bold,italic,bolditalic}.fntdata
  * adds a <Default Extension="fntdata" .../> entry to [Content_Types].xml
  * adds four font Relationships to ppt/_rels/presentation.xml.rels
  * adds embedTrueTypeFonts="1" + <p:embeddedFontLst> to ppt/presentation.xml

Idempotent: replaces any existing <p:embeddedFontLst> and skips duplicate
Relationships / Content-Types entries.

Run after `quarto render ... --to pptx`. Exits non-zero (with guidance) if the
required font blobs are missing."""

import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONTS_DIR = HERE.parent / "assets" / "fonts"  # common/utils/ → common/assets/fonts/

VARIANTS = [
    # (xml tag, blob filename, arcname inside pptx)
    ("regular",    "poppins-regular.fntdata",    "ppt/fonts/poppins-regular.fntdata"),
    ("bold",       "poppins-bold.fntdata",       "ppt/fonts/poppins-bold.fntdata"),
    ("italic",     "poppins-italic.fntdata",     "ppt/fonts/poppins-italic.fntdata"),
    ("boldItalic", "poppins-bolditalic.fntdata", "ppt/fonts/poppins-bolditalic.fntdata"),
]

FONT_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
FONT_CONTENT_TYPE = "application/x-fontdata"  # matches the original NCAR template

NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def check_fonts() -> dict[str, bytes]:
    missing = [b for _, b, _ in VARIANTS if not (FONTS_DIR / b).exists()]
    if missing:
        sys.exit(
            "embed_poppins: missing font blob(s): "
            + ", ".join(missing)
            + f"\n  expected under {FONTS_DIR}/"
        )
    return {b: (FONTS_DIR / b).read_bytes() for _, b, _ in VARIANTS}


def patch_content_types(xml: str) -> str:
    if 'Extension="fntdata"' in xml:
        return xml
    entry = f'<Default Extension="fntdata" ContentType="{FONT_CONTENT_TYPE}"/>'
    if '<Default Extension="rels"' in xml:
        return xml.replace(
            '<Default Extension="rels"', entry + '<Default Extension="rels"', 1
        )
    return xml.replace("</Types>", entry + "</Types>")


def next_rids(rels_xml: str, count: int) -> list[str]:
    existing = [int(m) for m in re.findall(r'Id="rId(\d+)"', rels_xml)]
    start = (max(existing) if existing else 0) + 1
    return [f"rId{start + i}" for i in range(count)]


def patch_rels(xml: str, rids: list[str]) -> str:
    # Skip any font rel already pointing at our targets (idempotency).
    existing_targets = set(re.findall(r'Target="(fonts/poppins-[^"]+\.fntdata)"', xml))
    to_add = []
    for (_, blob, arc), rid in zip(VARIANTS, rids):
        target = f"fonts/{blob}"
        if target in existing_targets:
            continue
        to_add.append(
            f'<Relationship Id="{rid}" '
            f'Type="{FONT_REL_TYPE}" '
            f'Target="{target}"/>'
        )
    if not to_add:
        return xml
    return xml.replace("</Relationships>", "".join(to_add) + "</Relationships>")


def effective_font_rids(rels_xml: str) -> dict[str, str]:
    """Map blob filename → rId, scanning the post-patch rels."""
    out = {}
    for rid, target in re.findall(
        r'<Relationship[^>]+Id="(rId\d+)"[^>]+Type="' + re.escape(FONT_REL_TYPE) + r'"[^>]+Target="fonts/(poppins-[^"]+\.fntdata)"',
        rels_xml,
    ):
        out[target] = rid
    return out


def build_embedded_font_lst(blob_to_rid: dict[str, str]) -> str:
    children = []
    for tag, blob, _ in VARIANTS:
        rid = blob_to_rid.get(blob)
        if rid is None:
            sys.exit(f"embed_poppins: internal error — no rId for {blob}")
        children.append(f'<p:{tag} r:id="{rid}"/>')
    return (
        "<p:embeddedFontLst>"
        "<p:embeddedFont>"
        '<p:font typeface="Poppins" pitchFamily="2" charset="77"/>'
        + "".join(children)
        + "</p:embeddedFont>"
        "</p:embeddedFontLst>"
    )


def patch_presentation_xml(xml: str, embedded_font_lst: str) -> str:
    # 1. Ensure embedTrueTypeFonts="1" is on the root <p:presentation> element.
    if "embedTrueTypeFonts=" not in xml:
        xml = re.sub(
            r"(<p:presentation\b)([^>]*)>",
            r'\1\2 embedTrueTypeFonts="1">',
            xml,
            count=1,
        )

    # 2. Replace any existing <p:embeddedFontLst>, or insert after <p:notesSz/>.
    if "<p:embeddedFontLst" in xml:
        return re.sub(
            r"<p:embeddedFontLst>.*?</p:embeddedFontLst>",
            embedded_font_lst,
            xml,
            count=1,
            flags=re.DOTALL,
        )

    # <p:notesSz cx=".." cy=".."/> (self-closing). Insert right after it.
    m = re.search(r"<p:notesSz[^/]*/>", xml)
    if not m:
        sys.exit("embed_poppins: couldn't locate <p:notesSz/> in presentation.xml")
    insert_at = m.end()
    return xml[:insert_at] + embedded_font_lst + xml[insert_at:]


def rewrite_pptx(pptx_path: Path, blobs: dict[str, bytes]) -> tuple[int, int]:
    with tempfile.NamedTemporaryFile(
        suffix=".pptx", dir=pptx_path.parent, delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(pptx_path, "r") as zin:
            # Pre-compute the patched rels so we know which rIds the embeddedFontLst uses.
            rels_xml = zin.read("ppt/_rels/presentation.xml.rels").decode("utf-8")
            rids = next_rids(rels_xml, len(VARIANTS))
            rels_xml_patched = patch_rels(rels_xml, rids)
            blob_to_rid = effective_font_rids(rels_xml_patched)

            efl = build_embedded_font_lst(blob_to_rid)

            pres_xml = zin.read("ppt/presentation.xml").decode("utf-8")
            pres_xml_patched = patch_presentation_xml(pres_xml, efl)

            ct_xml = zin.read("[Content_Types].xml").decode("utf-8")
            ct_xml_patched = patch_content_types(ct_xml)

            existing_names = set(zin.namelist())
            existing_arcs = {arc for _, _, arc in VARIANTS if arc in existing_names}

            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    name = item.filename
                    if name in {arc for _, _, arc in VARIANTS}:
                        # Will write fresh below; skip the existing copy.
                        continue
                    if name == "ppt/presentation.xml":
                        zout.writestr(item, pres_xml_patched.encode("utf-8"))
                    elif name == "ppt/_rels/presentation.xml.rels":
                        zout.writestr(item, rels_xml_patched.encode("utf-8"))
                    elif name == "[Content_Types].xml":
                        zout.writestr(item, ct_xml_patched.encode("utf-8"))
                    else:
                        zout.writestr(item, zin.read(name))
                # Inject (or re-inject) the font blobs.
                for _, blob, arc in VARIANTS:
                    zout.writestr(arc, blobs[blob])

        shutil.move(tmp_path, pptx_path)
        return len(VARIANTS), len(existing_arcs)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: embed_poppins.py <output.pptx>")
    pptx_path = Path(sys.argv[1]).resolve()
    if not pptx_path.exists():
        sys.exit(f"embed_poppins: no such file: {pptx_path}")

    blobs = check_fonts()
    variants, replaced = rewrite_pptx(pptx_path, blobs)
    action = "replaced" if replaced else "embedded"
    print(
        f"embed_poppins: {action} Poppins "
        f"({variants} variants) → {pptx_path.name}"
    )


if __name__ == "__main__":
    main()
