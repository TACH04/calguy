import os
from PIL import Image, ImageDraw, ImageFont
import datetime

# Fallback to default if Arial is not found (which it should be on macOS)
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"

def render_event_dashboard(events, output_path):
    """
    Renders a high-fidelity image dashboard of upcoming events.
    events: list of dicts with 'date', 'time', 'title', 'attendees' (int)
    """
    width = 850
    row_height = 60
    header_height = 100
    padding = 30
    
    height = header_height + max(1, len(events)) * row_height + padding * 2
    
    # Dark mode charcoal background
    img = Image.new("RGB", (width, height), color="#1e1e24")
    draw = ImageDraw.Draw(img)
    
    try:
        font_title = ImageFont.truetype(FONT_PATH, 36)
        font_header = ImageFont.truetype(FONT_PATH, 20)
        font_main = ImageFont.truetype(FONT_PATH, 24)
    except IOError:
        font_title = ImageFont.load_default(size=36) if hasattr(ImageFont, 'load_default') else ImageFont.load_default()
        font_header = ImageFont.load_default(size=20) if hasattr(ImageFont, 'load_default') else ImageFont.load_default()
        font_main = ImageFont.load_default(size=24) if hasattr(ImageFont, 'load_default') else ImageFont.load_default()

    # Draw Header Title
    draw.text((padding, padding), "🏔️ Brolympus Schedule", font=font_title, fill="#ffd700")  # Gold accent
    
    # Last updated timestamp
    now_str = datetime.datetime.now().strftime("%I:%M %p")
    draw.text((width - padding - 200, padding + 15), f"Updated: {now_str}", font=font_header, fill="#888888")

    # Column constraints
    cols = [padding, padding + 150, padding + 320, width - 150]
    col_names = ["DATE", "TIME", "EVENT", "ATTENDEES"]
    
    y = header_height
    for x, name in zip(cols, col_names):
        draw.text((x, y), name, font=font_header, fill="#aaaaaa")
        
    y += 35
    # Header separator
    draw.line([(padding, y), (width-padding, y)], fill="#555555", width=2)
    y += 20
    
    if not events:
        draw.text((padding, y + 20), "No upcoming events scheduled.", font=font_main, fill="#ffffff")
    else:
        for ev in events:
            # Draw fields
            draw.text((cols[0], y), ev['date'], font=font_main, fill="#ffffff")
            draw.text((cols[1], y), ev['time'], font=font_main, fill="#cccccc")
            
            # Truncate long titles
            title = ev['title']
            if len(title) > 23:
                title = title[:20] + "..."
            draw.text((cols[2], y), title, font=font_main, fill="#ffffff")
            
            # Attendee badge formatting
            att_count = ev['attendees']
            if att_count > 0:
                draw.text((cols[3], y), f"✅ {att_count} Going", font=font_main, fill="#4CAF50")
            else:
                draw.text((cols[3], y), "- None -", font=font_main, fill="#888888")
            
            y += row_height
            # Subtle row separator
            draw.line([(padding, y - 10), (width - padding, y - 10)], fill="#333333", width=1)
            
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path)
    return output_path
