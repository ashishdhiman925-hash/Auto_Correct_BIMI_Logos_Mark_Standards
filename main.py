
import streamlit as st
import re
import io
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom
import base64
# ────────────────────────────────────────────────
#                LOGIC FUNCTIONS
# ────────────────────────────────────────────────

def sanitize_content(content: bytes) -> bytes:
    """Removes non-breaking spaces and other common 'ghost' characters."""
    content = content.replace(b'\xc2\xa0', b' ')
    content = content.replace(b'&nbsp;', b' ')
    return content


def prettify(elem, strip_header=False):
    """
    Return pretty-printed XML string.
    If strip_header is True, it removes the <?xml ... ?> line.
    """
    rough_string = ET.tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)

    # Generate the string with UTF-8 encoding
    pretty_xml = reparsed.toprettyxml(indent="  ", encoding='utf-8').decode('utf-8')

    # Toggleable XML DECLARATION removal
    if strip_header and pretty_xml.startswith('<?xml'):
        lines = pretty_xml.splitlines()
        # Join everything from the second line onwards
        pretty_xml = "\n".join(lines[1:])

    return pretty_xml


def correct_bimi_svg(content: bytes, strip_header=False) -> tuple[bytes | None, list[str]]:
    messages = []
    content = sanitize_content(content)

    try:
        SVG_NS = "http://www.w3.org/2000/svg"
        ET.register_namespace('', SVG_NS)
        tree = ET.ElementTree(ET.fromstring(content))
        root = tree.getroot()
    except Exception as e:
        messages.append(f"❌ Cannot parse XML: {e}")
        return None, messages

    changed = False

    # 1. Force version="1.2" and baseProfile="tiny-ps"
    if root.get("version") != "1.2":
        root.set("version", "1.2")
        messages.append("→ Set version=\"1.2\"")
        changed = True

    if root.get("baseProfile") != "tiny-ps":
        root.set("baseProfile", "tiny-ps")
        messages.append("→ Set baseProfile=\"tiny-ps\"")
        changed = True

    # 2. Convert 'style' attributes to direct attributes for tiny-ps compliance
    # BIMI validators often fail when style strings are used instead of attributes.
    style_count = 0
    for elem in tree.iter():
        style_str = elem.get("style")
        if style_str:
            # Split style string (e.g., "fill:#020202;stroke:none") into pairs
            styles = [s.strip() for s in style_str.split(";") if ":" in s]
            for s in styles:
                prop, val = s.split(":", 1)
                prop = prop.strip().lower()
                val = val.strip()

                # Only move common visual properties to attributes
                if prop in {"fill", "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity"}:
                    elem.set(prop, val)

            # Remove the style attribute after migration
            del elem.attrib["style"]
            style_count += 1
            changed = True

    if style_count > 0:
        messages.append(f"→ Converted {style_count} 'style' strings to direct attributes.")

    # 3. Case-insensitive removal of forbidden attributes
    targets = {"x", "y", "width", "height", "overflow", "xml:space"}
    keys_to_delete = [k for k in root.attrib if k.lower() in targets]

    for key in keys_to_delete:
        del root.attrib[key]
        messages.append(f"→ Removed forbidden attribute: {key}")
        changed = True

    # --- Dimension & ViewBox Calculation ---
    curr_w = 0.0
    curr_h = 0.0
    target_dim = 96.0
    current_vb = root.get("viewBox")

    try:
        # Check attributes first
        w_attr = root.get('width')
        h_attr = root.get('height')
        if w_attr:
            curr_w = float(w_attr.replace('px', '').strip())
        if h_attr:
            curr_h = float(h_attr.replace('px', '').strip())
    except (ValueError, TypeError):
        pass

    if current_vb:
        try:
            v_box = [float(x) for x in current_vb.split()]
            if len(v_box) >= 4:
                curr_w = max(curr_w, v_box[2])
                curr_h = max(curr_h, v_box[3])
        except (ValueError, IndexError):
            pass

    side_length = max(curr_w, curr_h, target_dim)

    # --- Title Handling ---
    final_title_text = "Company Logo"
    titles_found = []
    for parent in tree.iter():
        for child in list(parent):
            if child.tag.endswith('title'):
                if child.text and child.text.strip():
                    final_title_text = child.text.strip()
                parent.remove(child)
                titles_found.append(child)

    # --- Centering and Squaring Logic ---
    group_tag = f"{{{SVG_NS}}}g"
    shift_x = (side_length - curr_w) / 2
    shift_y = (side_length - curr_h) / 2

    new_group = ET.Element(group_tag, {
        "id": "bimi-centered-group",
        "transform": f"translate({shift_x}, {shift_y})"
    })

    for child in list(root):
        new_group.append(child)
        root.remove(child)

    root.append(new_group)
    root.set("viewBox", f"0 0 {side_length} {side_length}")
    messages.append(f"→ Squared to {side_length}x{side_length} and centered content.")

    # --- Re-insert Title as Direct Child with Namespace ---
    title_tag = f"{{{SVG_NS}}}title"
    title_el = ET.Element(title_tag)
    title_el.text = final_title_text
    root.insert(0, title_el)

    if not titles_found:
        messages.append("→ Added missing <title> element as direct child.")
    else:
        messages.append("→ Validated <title> element as direct child of <svg>.")

    try:
        corrected_str = prettify(root, strip_header=strip_header)
        return corrected_str.encode('utf-8'), messages
    except Exception as e:
        messages.append(f"❌ Failed to generate corrected XML: {e}")
        return None, messages

# ────────────────────────────────────────────────
#                STREAMLIT UI
# ────────────────────────────────────────────────

st.set_page_config(page_title="BIMI SVG Automatic GCC Error Resolver", layout="wide")

st.sidebar.header("Settings")
strip_xml_header = st.sidebar.toggle(
    "Remove XML Header",
    value=False,
    help="Turn this on if GCC errors out due to the XML declaration."
)

st.title("🛠️ BIMI SVG Automatic GCC Error Resolver")
st.warning("Ensure file is < 32 KB. Use for internal SVG fixes only.")

uploaded_file = st.file_uploader("Upload your SVG", type=["svg"])


def clean_svg_markup(svg_text):
    """Removes forbidden BIMI tags and attributes."""
    forbidden_tags = ['script', 'animate', 'animateTransform', 'foreignObject', 'iframe', 'video', 'audio']
    for tag in forbidden_tags:
        svg_text = re.sub(rf'<{tag}.*?>.*?</{tag}>', '', svg_text, flags=re.IGNORECASE | re.DOTALL)
        svg_text = re.sub(rf'<{tag}.*?/>', '', svg_text, flags=re.IGNORECASE)

    svg_text = re.sub(r'\s(x|y|overflow|enable-background|xml:space)="[^"]*"', '', svg_text)
    svg_text = re.sub(r'<metadata.*?>.*?</metadata>', '', svg_text, flags=re.DOTALL)
    return svg_text


if uploaded_file is not None:
    original_bytes = uploaded_file.read()
    raw_text = original_bytes.decode("utf-8")
    cleaned_text = clean_svg_markup(raw_text)
    cleaned_bytes = cleaned_text.encode("utf-8")

    with st.spinner("Processing..."):
        corrected_bytes, log_messages = correct_bimi_svg(cleaned_bytes, strip_header=strip_xml_header)

    st.subheader("Action Log")
    if cleaned_text != raw_text:
        st.info("ℹ️ Security: Stripped forbidden elements/attributes.")

    for msg in log_messages:
        if "❌" in msg:
            st.error(msg)
        else:
            st.info(msg)

    if corrected_bytes:
        st.divider()
        col1, col2 = st.columns([1, 2])
        with col1:
            st.success("BIMI Ready!")
            st.download_button(
                label="⬇️ Download Corrected SVG",
                data=corrected_bytes,
                file_name=f"{Path(uploaded_file.name).stem}-bimi.svg",
                mime="image/svg+xml"
            )
            b64_svg = base64.b64encode(corrected_bytes).decode("utf-8")
            st.markdown(
                f'<img src="data:image/svg+xml;base64,{b64_svg}" width="150" style="background-color: white; padding: 5px; border-radius: 5px;">',
                unsafe_allow_html=True)

        with col2:
            with st.expander("Show Cleaned XML Code"):
                st.code(corrected_bytes.decode('utf-8'), language="xml")

