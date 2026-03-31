
import streamlit as st
import re
import io
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom
import base64


# ────────────────────────────────────────────────
#               LOGIC FUNCTIONS
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
        ET.register_namespace('', "http://www.w3.org/2000/svg")
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

    # 2. Case-insensitive removal of forbidden attributes
    targets = {"x", "y", "width", "height", "overflow", "xml:space"}
    keys_to_delete = [k for k in root.attrib if k.lower() in targets]

    for key in keys_to_delete:
        del root.attrib[key]
        messages.append(f"→ Removed forbidden attribute: {key}")
        changed = True
# --- 1. Initialize Variables ---
    # We start with 0.0 to prevent UnboundLocalError
    curr_w = 0.0
    curr_h = 0.0
    target_dim = 96.0
    current_vb = root.get("viewBox")

    # --- 2. Extract from Attributes (Handles decimals like 691.625) ---
    try:
        w_attr = root.get('width')
        h_attr = root.get('height')
        if w_attr:
            # Strip 'px' and spaces, then convert to float
            curr_w = float(w_attr.replace('px', '').strip())
        if h_attr:
            curr_h = float(h_attr.replace('px', '').strip())
    except (ValueError, TypeError):
        pass

    # --- 3. Handle ViewBox & "Highest Value" Logic ---
    # This addresses Requirement 2: Use maximum if viewBox/attributes differ
    if current_vb:
        try:
            v_box = [float(x) for x in current_vb.split()]
            if len(v_box) >= 4:
                # We take the larger of the existing value vs the viewBox value
                curr_w = max(curr_w, v_box[2])
                curr_h = max(curr_h, v_box[3])
        except (ValueError, IndexError):
            pass

    # --- 4. Determine final side length (Requirement 1 & 3) ---
    # This picks the largest side found, but ensures it is AT LEAST 96.0
    side_length = max(curr_w, curr_h, target_dim)

    # --- 5. Apply the Centering and Squaring Logic ---
    # Check if we need to change it (if it's not square or too small)
    if curr_w != side_length or curr_h != side_length or not current_vb:
        
        # Calculate shifts to center the content within the new square
        shift_x = (side_length - curr_w) / 2
        shift_y = (side_length - curr_h) / 2

        # Create the centering group
        new_group = ET.Element("g", {
            "id": "bimi-centered-group",
            "transform": f"translate({shift_x}, {shift_y})"
        })

        # Move all elements into this group
        for child in list(root):
            # Skip the group itself if it was somehow already there
            if child != new_group:
                new_group.append(child)
                root.remove(child)

        # Update the root
        root.append(new_group)
        
        # Update viewBox to be a perfect square (Requirement 3)
        new_target_vb = f"0 0 {side_length} {side_length}"
        root.set("viewBox", new_target_vb)

        messages.append(f"→ Squared to {side_length}x{side_length}. Centered with: ({shift_x}, {shift_y})")
        changed = True
#     # 3. Force viewBox to a perfect square (96x96)
#     # ... (inside your processing loop)
#     target_dim = 96
#     target_vb = f"0 0 {target_dim} {target_dim}"
#     current_vb = root.get("viewBox")
#     # Adding this code to make sure its forcing 96 by 96 if less but also make other as Square if need be : 
#     if current_vb:
#         v_box = [float(x) for x in current_vb.split()]
#         curr_w = v_box[2]
#         curr_h = v_box[3]

#     # 1. Determine the side length for the new square viewBox.
#     # This takes the largest side, but ensures it is at least 'target_dim' (96).
#     # 1. Initialize variables to 0.0 to prevent UnboundLocalError
#     curr_w = 0.0
#     curr_h = 0.0
#     target_dim = 96.0

# # 2. Try to get width/height from the <svg> attributes first
#     try:
#         if root.get('width'):
#         # Using float() handles the decimals like 691.625
#             curr_w = float(root.get('width').replace('px', '').strip())
#         if root.get('height'):
#             curr_h = float(root.get('height').replace('px', '').strip())
#     except (ValueError, TypeError):
#         pass

# # 3. If a viewBox exists, it should take priority for dimensions
#     if current_vb:
#         try:
#             v_box = [float(x) for x in current_vb.split()]
#             if len(v_box) >= 4:
#                 # Overwrite width/height with the viewBox values
#                 curr_w = v_box[2]
#                 curr_h = v_box[3]
#         except (ValueError, IndexError):
#             pass

# # 4. Determine the side length for the new square.
# # This picks the largest of all values (width, height, or 96).
#     side_length = max(curr_w, curr_h, target_dim)

# # Now side_length is your new width AND new height for a perfect square.
#     side_length = max(curr_w, curr_h, target_dim)

#     # 2. Check if we actually need to change anything. 
#     # We change it if it's not square OR if it's smaller than the target.
#     if curr_w != side_length or curr_h != side_length:
        
#         # 3. Calculate shifts to center the content within the new square
#         shift_x = (side_length - curr_w) / 2
#         shift_y = (side_length - curr_h) / 2

#         # 4. Create the centering group
#         new_group = ET.Element("g", {
#             "transform": f"translate({shift_x}, {shift_y})"
#         })

#         # 5. Move all elements into this group
#         for child in list(root):
#             new_group.append(child)
#             root.remove(child)

#         # 6. Update the root
#         root.append(new_group)
        
#         # 7. Update viewBox to be a perfect square: "0 0 side side"
#         new_target_vb = f"0 0 {side_length} {side_length}"
#         root.set("viewBox", new_target_vb)

#         messages.append(f"→ Squared to {side_length}x{side_length}. Centered with: ({shift_x}, {shift_y})")
#         changed = True
    # if current_vb:
    #     v_box = [float(x) for x in current_vb.split()]
    #     curr_w = v_box[2]
    #     curr_h = v_box[3]

    #     if curr_w < target_dim or curr_h < target_dim:
    #         # 1. Calculate the necessary shift to center it
    #         shift_x = (target_dim - curr_w) / 2
    #         shift_y = (target_dim - curr_h) / 2

    #         # 2. Create a new group to hold all current children
    #         new_group = ET.Element("g", {
    #             "transform": f"translate({shift_x}, {shift_y})"
    #         })

    #         # 3. Move all elements into this group
    #         for child in list(root):
    #             new_group.append(child)
    #             root.remove(child)

    #         # 4. Add the group back to the root and update viewBox
    #         root.append(new_group)
    #         root.set("viewBox", target_vb)

    #         messages.append(f"→ Centered content with translation: ({shift_x}, {shift_y})")
    #         changed = True
    # target_vb = "0 0 96 96"
    # current_vb = root.get("viewBox")
    #
    # if current_vb <= target_vb:
    #     root.set("viewBox", target_vb)
    #     messages.append(f"→ Resized viewBox to square: \"{target_vb}\"")
    #     changed = True

    # 4. Add <title> if missing
    has_title = any(el.tag.endswith('}title') for el in root)
    if not has_title:
        title_el = ET.Element("{http://www.w3.org/2000/svg}title")
        title_el.text = "Company Logo"
        root.insert(0, title_el)
        messages.append("→ Added missing <title> element")
        changed = True

    try:
        corrected_str = prettify(root, strip_header=strip_header)
        return corrected_str.encode('utf-8'), messages
    except Exception as e:
        messages.append(f"❌ Failed to generate corrected XML: {e}")
        return None, messages


# ────────────────────────────────────────────────
#               STREAMLIT UI
# ────────────────────────────────────────────────

st.set_page_config(page_title="BIMI SVG Automatic GCC Error Resolver", layout="wide")

# Sidebar Configuration
st.sidebar.header("Settings")
strip_xml_header = st.sidebar.toggle(
    "Remove XML Header",
    value=False,
    help="Their can be case that our GCC BIMI check error out : Turn this on to remove; if needed <?xml version='1.0' ... ?>."
)

st.title("🛠️ BIMI SVG Automatic GCC Error Resolver")
st.warning("""
    Please ensure that your file is less than 32 KB.
    Use it for internal SVG fix only.
    Do not expose this to external customers.
""")
st.markdown("Transforming SVGs into **BIMI-compliant Tiny P/S** files with a forced squared aspect ratio.")

uploaded_file = st.file_uploader("Upload your SVG", type=["svg"])

if uploaded_file is not None:
    original_bytes = uploaded_file.read()

    with st.spinner("Processing..."):
        corrected_bytes, log_messages = correct_bimi_svg(original_bytes, strip_header=strip_xml_header)

    # Action Log
    st.subheader("Action Log")
    for msg in log_messages:
        if "❌" in msg:
            st.error(msg)
        elif "⚠️" in msg:
            st.warning(msg)
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
    # Encode the bytes to base64 so the browser can read it as a URI
    try:
        b64_svg = base64.b64encode(corrected_bytes).decode("utf-8")
        img_html = f'<img src="data:image/svg+xml;base64,{b64_svg}" width="150" style="background-color: white; padding: 5px; border-radius: 5px;">'
        st.markdown(img_html, unsafe_allow_html=True)
    except Exception:
        st.info("Preview unavailable, but download is ready.")
        # with col1:
        #     st.success("BIMI Ready!")
        #     st.download_button(
        #         label="⬇️ Download Corrected SVG",
        #         data=corrected_bytes,
        #         file_name=f"{Path(uploaded_file.name).stem}-bimi.svg",
        #         mime="image/svg+xml"
        #     )
        #     st.image(corrected_bytes, width=150)
        #     # --- Silent Approach applied here ---
        #     try:
        #         st.image(corrected_bytes, width=150)
        #     except Exception:
        #         # Optionally show a tiny hint, or leave it blank
        #         st.caption("✨ BIMI File generated (Preview unavailable)")
        with col2:
            with st.expander("Show Cleaned XML Code"):
                st.code(corrected_bytes.decode('utf-8'), language="xml")
