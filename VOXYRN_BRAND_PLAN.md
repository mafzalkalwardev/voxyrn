# Voxyrn brand and launch plan

## Product position

Voxyrn is an always-on sales automation platform for outbound campaigns. It combines campaign execution, natural voice conversations, speech recognition, AI responses, voicemail handling, lead capture, CRM records, recordings, and follow-up workflows.

## Primary message

**Voxyrn keeps every campaign moving.**

Supporting line:

**Always-on voice automation for reaching, qualifying, and following up with more prospects.**

## SEO foundation

Primary title:

```text
Voxyrn | Always-On Sales Automation
```

Meta description:

```text
Voxyrn automates outbound sales campaigns with natural voice conversations, speech intelligence, lead capture, voicemail handling, and follow-up workflows.
```

Suggested keywords:

```text
sales automation
outbound sales automation
AI calling platform
voice AI sales agent
automated sales campaigns
lead qualification automation
AI follow-up software
conversational sales automation
campaign automation platform
always-on sales team
```

## Visual identity

- Deep navy background: reliable, technical, operational.
- Cyan-to-violet route mark: movement, intelligence, connectivity.
- Warm orange node: human action and conversion.
- Rounded geometry: approachable product, not a legacy call-center interface.
- Wordmark: `Voxyrn`, capital R only.

Assets:

```text
src/static/voxyrn-logo.svg
src/static/voxyrn-mark.svg
src/static/site.webmanifest
```

## Product naming rules

- Use `Voxyrn` in user-facing titles, navigation, browser metadata, README copy, installer labels, and screenshots.
- Use “campaigns”, “prospects”, “conversations”, “sales automation”, and “follow-up”.
- Do not use the former company identity in new product-facing copy.
- Keep provider-specific implementation names only where required for technical operation or legal/account setup.
- Do not claim universal carrier/provider compatibility until each integration is tested.

## Rollout

1. Keep the original working project untouched.
2. Develop in `GoogleVoiceAgent-AMD-Piper` on `piper-amd-experiment`.
3. Create and push a separate public repository named `Voxyrn`.
4. Verify the baseline application before changing runtime behavior.
5. Add branding and SEO metadata first.
6. Add latency instrumentation.
7. Add Piper behind a configuration flag.
8. Add AMD in shadow mode.
9. Run controlled local and live tests.
10. Update the public repository only after each tested milestone.

## Logo usage

- Use `voxyrn-mark.svg` for favicon, app icon, compact navigation, and social avatar.
- Use `voxyrn-logo.svg` for dark dashboard/sidebar surfaces.
- Preserve the SVG viewBox and do not stretch the mark independently.
- Use the mark on light surfaces only with a dark background tile.
