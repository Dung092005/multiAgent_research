---
name: Dispute Desk Design System
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#3d4947'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#6d7a77'
  outline-variant: '#bcc9c6'
  surface-tint: '#006a61'
  primary: '#00685f'
  on-primary: '#ffffff'
  primary-container: '#008378'
  on-primary-container: '#f4fffc'
  inverse-primary: '#6bd8cb'
  secondary: '#565e74'
  on-secondary: '#ffffff'
  secondary-container: '#dae2fd'
  on-secondary-container: '#5c647a'
  tertiary: '#4f5d72'
  on-tertiary: '#ffffff'
  tertiary-container: '#67758c'
  on-tertiary-container: '#fdfcff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#89f5e7'
  primary-fixed-dim: '#6bd8cb'
  on-primary-fixed: '#00201d'
  on-primary-fixed-variant: '#005049'
  secondary-fixed: '#dae2fd'
  secondary-fixed-dim: '#bec6e0'
  on-secondary-fixed: '#131b2e'
  on-secondary-fixed-variant: '#3f465c'
  tertiary-fixed: '#d5e3fd'
  tertiary-fixed-dim: '#b9c7e0'
  on-tertiary-fixed: '#0d1c2f'
  on-tertiary-fixed-variant: '#3a485c'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  display-lg:
    fontFamily: Geist
    fontSize: 30px
    fontWeight: '600'
    lineHeight: 38px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Geist
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  title-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  label-md:
    fontFamily: Geist
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-xs:
    fontFamily: Geist
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 20px
  margin-mobile: 16px
  margin-desktop: 32px
---

## Brand & Style
The design system is engineered for high-stakes B2B e-commerce operations. The brand personality is **calm, precise, and deterministic**, prioritizing utility over decoration. It targets operations managers and legal specialists who require a high-density information environment that minimizes cognitive load.

The visual style is **Modern B2B SaaS**, characterized by:
- **Systematic Precision:** Alignment with a strict grid to evoke a sense of order and reliability.
- **Atmospheric Clarity:** Utilizing high-contrast typography against clean, neutral surfaces.
- **Functional Minimalism:** Removing unnecessary ornamentation to focus entirely on data integrity and actionability.
- **Institutional Trust:** A professional aesthetic that suggests the robustness of a financial or legal platform.

## Colors
This design system utilizes a structured palette designed for long-duration focused work. 

- **Primary & Accents:** The Teal (#0D9488) serves as the singular "action" color, used exclusively for primary interactions and critical success confirmations.
- **Neutrals:** Deep Slate (#0F172A) and Ink (#1E293B) provide the structural foundation, used for persistent navigation elements and primary headings to anchor the eye.
- **Semantic States:** Status colors (Crimson, Amber, Blue, Emerald) use a "Light Background / Dark Text" formula for badges. Ensure a minimum contrast ratio of 4.5:1 for all semantic text.
- **Surfaces:** Use #F8FAFC for application backgrounds to reduce screen glare, with #FFFFFF reserved for elevated cards and data containers.

## Typography
The typography system balances the technical aesthetic of **Geist** for headings and labels with the exceptional legibility of **Inter** for body text and data entry.

- **Data Density:** Use `body-sm` (13px) for table rows and secondary metadata to maximize the information visible on a single screen.
- **Hierarchy:** Headings should always use the Slate/Ink neutrals to distinguish them from interactive Teal elements.
- **Numerical Data:** For tables containing currency or dates, utilize the tabular numbers feature (tnum) of the font to ensure vertical alignment.

## Layout & Spacing
The system employs a **Fixed-Fluid Hybrid Grid**. 

- **Sidebar:** Fixed at 260px to maintain consistent navigation.
- **Main Content:** A 12-column fluid grid with a maximum container width of 1440px to prevent excessive line lengths in text-heavy dispute documents.
- **Density Model:** A strict 4px baseline grid governs all spacing. For data-intensive views (e.g., Dispute Queues), use "Compact" spacing (8px cell padding). For editorial or dashboard views, use "Default" spacing (16px - 24px).
- **Responsive Behavior:** At 1024px and below, the sidebar collapses into a narrow icon-only rail or hidden drawer to prioritize the workspace.

## Elevation & Depth
Depth is communicated through **Tonal Layering** and **Low-Contrast Outlines** rather than dramatic shadows.

- **Level 0 (Canvas):** #F8FAFC - The base application background.
- **Level 1 (Cards/Panels):** #FFFFFF - White surfaces with a 1px border (#E2E8F0).
- **Level 2 (Popovers/Modals):** #FFFFFF - Requires a subtle, diffused shadow: `0px 4px 12px rgba(15, 23, 42, 0.08)`.
- **Active State:** Elements being dragged or interacted with use a slight tint of the Primary Teal as a background wash (5% opacity) to indicate focus.

## Shapes
This design system uses **Soft (0.25rem)** rounding to maintain a professional, structured feel that isn't overly organic or "bubbly."

- **Standard Elements:** Buttons, Inputs, and Badges use `rounded-sm` (4px).
- **Containers:** Large cards and dashboard panels use `rounded-lg` (8px).
- **Special Elements:** Stepper nodes and confidence meters may use `rounded-full` for distinct visual differentiation from data fields.

## Components
Consistent implementation of components ensures the "Olist" operational standard:

- **Buttons:** Primary buttons are solid Teal (#0D9488) with white text. Secondary buttons use a white background with a 1px Slate border. Text is always `label-md` weight.
- **Status Badges:** Use a pill shape with 10% opacity of the semantic color for the background and 100% opacity for the text (e.g., Error: #FEF2F2 bg / #991B1B text).
- **Data Tables:** Headers must be `label-xs` (uppercase) with a subtle bottom border. Rows should have a hover state of #F1F5F9. Vertical borders are discouraged; use horizontal spacing for column separation.
- **Confidence Meters:** A horizontal segmented bar. Use a scale from Error Crimson to Primary Teal. The active segment is highlighted with a 2px offset border.
- **Pipeline Steppers:** Use a linear, non-clickable visual at the top of dispute files. Completed steps are Teal; the current step is Slate; future steps are Light Gray.
- **Input Fields:** 1px border (#E2E8F0) that transitions to Teal (#0D9488) on focus with a 2px soft glow of the same color.