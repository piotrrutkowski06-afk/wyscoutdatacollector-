"""Inline rendering for a screenshot delivered only to the current panel session."""
from base64 import b64encode


# This release sends st.html deltas directly to the session without the legacy
# shared message cache. Do not use st.image or a file/download URL here.
PREVIEW_STREAMLIT_VERSION = '1.67.0'
PREVIEW_MAX_BYTES = 1_500_000


def preview_markup(jpeg):
    """Accept only bounded JPEG bytes; never interpolate website HTML or a URL."""
    if not jpeg or len(jpeg) > PREVIEW_MAX_BYTES or not jpeg.startswith(b'\xff\xd8\xff'):
        return None
    encoded = b64encode(jpeg).decode('ascii')
    return f'<img src="data:image/jpeg;base64,{encoded}" width="100%" alt="Prywatny podgląd ekranu Wyscout">'
