# Design System: The Auditory Atelier

## 1. Overview & Creative North Star
**Creative North Star: "The Private Listening Lounge"**
This design system is not a utility; it is an artifact. It moves away from the "utility-first" clutter of mainstream streaming apps and toward a high-end, editorial experience reminiscent of a luxury watch lookbook or a bespoke vinyl collection. 

The aesthetic strategy breaks the traditional mobile grid through **intentional asymmetry** and **tonal depth**. By utilizing extreme typographic contrast—pairing oversized, delicate serifs with technical monospaced data—we create an environment that feels both heritage-inspired and cutting-edge. We avoid the "templated" look by treating the mobile screen as a canvas for layering light and texture rather than just a container for buttons.

---

## 2. Colors & Surface Architecture
The palette is rooted in deep obsidian and charcoal tones, punctuated by metallic gold highlights that mimic the glow of vacuum tubes in a high-end amplifier.

### The Color Palette (Material Logic)
- **Primary Base:** `#e6c364` (Primary Gold)
- **Primary Container:** `#c9a84c` (Burnished Gold)
- **Surface (Base):** `#131316`
- **Surface Container Lowest:** `#0e0e10` (Deepest Void)
- **Surface Container High:** `#2a2a2c` (Elevated Surface)
- **On-Surface (Text):** `#ede8df` (Off-White/Parchment)
- **Muted/Outline:** `#7a7469` (Champagne Grey)

### The "No-Line" Rule
Prohibit the use of 1px solid borders for sectioning content. To define boundaries, designers must use **Background Color Shifts**. For example, a tracklist container (Surface Container Low) should sit directly on the Background without a stroke. Separation is achieved through the subtle shift in value, creating a seamless, "molded" appearance.

### Surface Hierarchy & Nesting
Treat the UI as physical layers of stacked obsidian glass. 
- **The Base:** Use `surface_dim` (#131316) for the main background.
- **The Nested Layer:** Use `surface_container_lowest` (#0e0e10) for inset areas like search bars or inactive players to create a "carved out" look.
- **The Active Layer:** Use `surface_container_high` (#2a2a2c) for elements that need to feel closer to the user, such as active song cards.

### The "Glass & Gradient" Rule
Standard flat colors lack soul. For hero moments (e.g., the Now Playing screen), use **Glassmorphism**:
- Apply `surface_variant` at 40% opacity with a 20px `backdrop-blur`.
- Use a subtle linear gradient on primary CTAs: `primary` (#e6c364) to `primary_container` (#c9a84c) at a 135-degree angle to simulate a metallic sheen.

---

## 3. Typography
The typographic soul of this system lies in the tension between the organic curves of the Serif and the cold precision of the Mono.

- **Display & Headlines (Cormorant Garamond):** Use for artist names and album titles. These should be large and expressive. Set with tight tracking (-2%) to feel editorial.
- **UI & Navigation (DM Sans):** Use for functional labels, buttons, and secondary metadata. It provides a clean, modern counterpoint to the serif.
- **Stats & Technical Data (DM Mono):** Use for timestamps, bitrates (e.g., "24-bit / 192kHz"), and track numbers. This reinforces the "Hi-Fi" technical precision of the brand.

**Hierarchy Note:** Always lead with the Serif. Even in small headers, a `title-sm` Serif conveys more luxury than a bold Sans-Serif.

---

## 4. Elevation & Depth
In this design system, shadows are light, not dark.

- **The Layering Principle:** Avoid "Drop Shadows" in the traditional sense. Achieve lift by placing a `surface_container_highest` element over a `surface_dim` background. The delta in color value provides all the "shadow" needed.
- **Ambient Light Shadows:** If an element must float (e.g., a volume slider handle), use a shadow color tinted with the `primary` gold at 5% opacity. The blur should be massive (30px+) with 0 offset to create a "glow" rather than a shadow.
- **The Ghost Border:** If accessibility requires a container edge, use the `outline_variant` token at **15% opacity**. It should feel like a faint reflection on the edge of a glass pane, never a solid line.

---

## 5. Components

### Buttons
- **Primary:** Gradient fill (Gold to Burnished Gold), `DM Sans` Bold, all-caps. Corners at `16px` (xl).
- **Secondary (The Outline):** No fill. A "Ghost Border" of 10% Gold.
- **Tertiary:** Text-only, `DM Mono` for a technical, understated look.

### Cards & Lists
- **The Rule of Zero Dividers:** Never use a horizontal line to separate tracks in a list. Use `16px` of vertical whitespace. If separation is visually required, use a alternating background shift of 1% (from `surface` to `surface_container_low`).

### Interactive Sliders (Progress Bar)
- **Track:** `surface_container_highest`.
- **Progress:** `primary` Gold.
- **Indicator:** Use a small `primary_fixed` dot that only appears on touch.

### Additional Signature Component: The "Metadata Cluster"
A technical block using `DM Mono` that displays the file format (FLAC/ALAC) and sample rate. Styled in a small `label-sm` with a `Ghost Border` pill, placed asymmetrically in the corner of album art.

---

## 6. Do’s and Don’ts

### Do:
- **Do** use negative space aggressively. High-end design breathes.
- **Do** use "Optical Centering." Serifs often look off-center due to their serifs; adjust them manually to feel balanced.
- **Do** utilize the `surface_container_lowest` for "cut-out" interaction states (e.g., a pressed button should look like it’s sinking into the surface).

### Don't:
- **Don't** use pure black (#000000). It kills the depth of the "obsidian" surfaces.
- **Don't** use standard Material icons. Use "Thin" or "Light" weight stroke icons (1px or 1.5px) to match the elegance of the Cormorant Garamond font.
- **Don't** use 100% opacity for muted text. Use `on_surface_variant` at 60-70% to let the background tone bleed through.