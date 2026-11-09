---
title: "Accessible UI"
description: "The web app meets WCAG 2.2 AA for people who couldn't be in the room"
when: "A PR modifies files under web/src/"
actions: "Comment on controls without an accessible name, state shown only by colour, missing focus styles, or results that load without a live-region announcement"
type: check
---

# Accessible UI

Debrief is pitched as the conference for people who couldn't be in the room, so the app itself must be usable by keyboard and screen reader. In changed `.tsx` files, flag: buttons, links, inputs or chips without an accessible name; state conveyed only by colour (e.g. a selected chip with no text, icon or `aria-pressed`); removed focus outlines; animations that ignore `prefers-reduced-motion`; and playlist or memo results that appear without an `aria-live` announcement.
