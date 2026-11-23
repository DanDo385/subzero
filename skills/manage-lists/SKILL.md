---
name: manage-lists
description: >
  Lists are not stored in Subzero. Use when the user asks to add, complete,
  move, or read a list. Tell them a list app manages lists. Do not call the
  old list tools and do not invent a list from memory.
---

# Manage lists

Subzero is not the list keeper. The list tools may still be registered. Do not
call them, and do not add new list tools.

## Trigger

"Add X to my list", "what's on my list", "mark X done", "move X from A to B".

## Procedure

1. Say that lists live in a list app, which connects to Subzero and manages
   them.
2. Do not call `list_search`, `list_create`, `list_add_item`, or the other
   list tools.
3. Do not write a Markdown list and call it saved.
4. A note the person should find later goes to the vault with `note_write`.
   A job for another agent goes to `dispatch`.

## Example

> "Add Morgan Stanley to my networking follow-up list."

Reply that a list app keeps that list. Do not create it in Subzero.
