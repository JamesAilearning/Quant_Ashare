# Proposal: Extend Operator UI Foundation Components

## Problem

The operator UI design-system foundation has centralized tokens and a small
set of display helpers, but the ticketed UI work still lacks a reusable common
component surface. Page-level follow-ups for Jobs, Config & Run, Pipeline
Results, and Walk-Forward would otherwise continue to duplicate button, table,
tag, dialog, loading, and feedback patterns.

## Scope

Extend the operator-facing UI foundation with:

- reusable Streamlit HTML component helpers for common controls and states;
- CSS classes for buttons, tags, cards, tabs, accordions, modals, toasts,
  tooltips, progress, form fields, and accessible tables;
- a localStorage-backed theme/color-convention browser contract while keeping
  the existing server-side preference file as a compatibility fallback;
- a richer design-system demo page that showcases the common components.

## Non-goals

- No runtime trading behavior changes.
- No official metric computation changes.
- No artifact schema changes.
- No Jobs, Config & Run, Pipeline Result, or Walk-Forward page-level workflow
  refactor beyond token-aligned presentation cleanup.
- No React/FastAPI migration; this remains a Streamlit-compatible foundation.

## Governance

This change is presentation infrastructure only. It does not select providers,
launch runs, reinterpret canonical qlib metrics, or promote research artifacts
into official runtime behavior.
