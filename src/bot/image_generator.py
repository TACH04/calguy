import os
from PIL import Image, ImageDraw, ImageFont
import datetime

# Fallback to default if Arial is not found (which it should be on macOS)
# Prefer Avenir for a premium look, fallback to Helvetica or Arial
# Font path priorities for Regular and Bold variants
REGULAR_FONT_PRIORITIES = [
    ("/System/Library/Fonts/Avenir.ttc", 0), # Avenir Book
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
]

BOLD_FONT_PRIORITIES = [
    ("/System/Library/Fonts/Avenir.ttc", 4), # Avenir Heavy
    ("/System/Library/Fonts/Helvetica.ttc", 2), # Helvetica Bold usually index 2 in TTC
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("/System/Library/Fonts/Avenir.ttc", 8), # Fallback to Medium if Heavy fails (unlikely)
]

def get_font(size, is_bold=False):
    """
    Safely loads a prioritized font from the system with fallback to default.
    """
    priorities = BOLD_FONT_PRIORITIES if is_bold else REGULAR_FONT_PRIORITIES
    
    for path, index in priorities:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=index)
            except (IOError, Exception):
                continue
    
    # Final reliable fallback
    try:
        # PIL >= 9.2.0 supports size in load_default
        return ImageFont.load_default(size=size)
    except (TypeError, AttributeError):
        return ImageFont.load_default()

def render_event_dashboard(events, output_path):
    """
    Renders a high-fidelity image dashboard of upcoming events.
    events: list of dicts with 'schedule', 'title', 'attendees' (int), 'attendees_data' (list)
    """
    width = 1000
    base_row_height = 60
    header_height = 100
    padding = 30
    line_spacing = 55 # Increased for bigger bubbles
    bubble_radius = 22
    bubble_diameter = bubble_radius * 2
    
    # Pre-load fonts for measurement
    font_title = get_font(36, is_bold=False)
    font_header = get_font(20, is_bold=False)
    font_main = get_font(24, is_bold=False)
    font_bubble = get_font(18, is_bold=True)

    # Column constraints
    cols = [padding, padding + 220, width - 420]
    
    # Calculate heights and layouts for each event
    # We use a dummy draw object for measurements
    dummy_img = Image.new("RGB", (1, 1))
    draw_measure = ImageDraw.Draw(dummy_img)
    
    event_layouts = []
    total_events_height = 0
    
    if not events:
        total_events_height = base_row_height + 20
    else:
        for ev in events:
            att_count = ev.get('attendees', 0)
            attendees_data = ev.get('attendees_data', [])
            
            layout = {
                'event': ev,
                'rows': [], # List of lists of {initials, color, x, y_offset}
                'height': base_row_height,
                'base_text': f"{att_count} Going" if att_count > 0 else "- None -"
            }
            
            if att_count > 0:
                # Fixed indentation for all initials rows
                start_x = cols[2] + 120
                current_x = start_x
                current_row = []
                row_y_offset = (base_row_height - bubble_diameter) // 2 - 5 # Center vertically in row
                
                for person in attendees_data:
                    # For bubble calculation, use diameter plus gap
                    if current_x + bubble_diameter > width - padding:
                        # Wrap to next line
                        layout['rows'].append(current_row)
                        current_row = []
                        current_x = start_x 
                        row_y_offset += line_spacing
                        
                    current_row.append({
                        'initials': person.get('initials', '?'),
                        'color': person.get('color', '#ffffff'),
                        'x': current_x,
                        'y_offset': row_y_offset
                    })
                    current_x += bubble_diameter + 10 # Spacing between bubbles
                
                if current_row:
                    layout['rows'].append(current_row)
                
                layout['height'] = max(base_row_height, row_y_offset + bubble_diameter + 15)
            
            event_layouts.append(layout)
            total_events_height += layout['height']

    height = header_height + total_events_height + padding * 2
    
    # Dark mode charcoal background
    img = Image.new("RGB", (width, height), color="#1e1e24")
    draw = ImageDraw.Draw(img)
    
    # Draw Header Title
    draw.text((padding, padding), "Brolympus Schedule", font=font_title, fill="#ffd700")  # Gold accent
    
    # Last updated timestamp
    now_str = datetime.datetime.now().strftime("%I:%M %p")
    draw.text((width - padding - 200, padding + 15), f"Updated: {now_str}", font=font_header, fill="#888888")

    col_names = ["SCHEDULE", "EVENT", "GOING"]
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
        for layout in event_layouts:
            ev = layout['event']
            # Draw fields
            schedule_text = ev.get('schedule', f"{ev.get('date', '')} {ev.get('time', '')}")
            draw.text((cols[0], y), schedule_text, font=font_main, fill="#ffffff")
            
            # Truncate long titles
            title = ev['title']
            if len(title) > 23:
                title = title[:20] + "..."
            draw.text((cols[1], y), title, font=font_main, fill="#ffffff")
            
            # Attendee base text (e.g. "X Going")
            att_color = "#4CAF50" if ev['attendees'] > 0 else "#888888"
            draw.text((cols[2], y), layout['base_text'], font=font_main, fill=att_color)
            
            # Draw all wrapped bubbles
            for row in layout['rows']:
                for item in row:
                    bx, by = item['x'], y + item['y_offset']
                    # Draw border circle
                    draw.ellipse([bx, by, bx + bubble_diameter, by + bubble_diameter], outline=item['color'], width=2)
                    
                    # Center initials inside
                    initials = item['initials']
                    ibbox = draw.textbbox((0, 0), initials, font=font_bubble)
                    iw = ibbox[2] - ibbox[0]
                    ih = ibbox[3] - ibbox[1]
                    
                    text_x = bx + (bubble_diameter - iw) // 2
                    text_y = by + (bubble_diameter - ih) // 2 - 2 # Minor visual adjustment
                    draw.text((text_x, text_y), initials, font=font_bubble, fill=item['color'])
            
            y += layout['height']
            # Subtle row separator
            draw.line([(padding, y - 5), (width - padding, y - 5)], fill="#333333", width=1)
            
    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path)
    return output_path
