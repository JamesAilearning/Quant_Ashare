# Proposal: Add Operator UI Design System Foundation

## Problem

The Streamlit operator UI has grown page by page. Shared visual tokens,
theme preferences, and formatting helpers are still thin, so each future
page refactor risks inventing its own colors, numeric formatting, and
empty-value display behavior.

## Scope

Add a foundation-only design system for the operator UI:

- centralized CSS custom properties for surface, text, border, semantic,
  chart, typography, spacing, radius, shadow, and motion tokens;
- persisted operator appearance preferences for theme and color convention;
- formatting helpers for percentages, numbers, money, durations, relative
  times, and absolute dates;
- a small design-system demo page for QA and future UI PRs.

## Non-goals

- No pipeline, walk-forward, backtest, attribution, or metric semantics change.
- No new official metric computation path.
- No page-level refactor of Jobs, Config & Run, Results, or Walk-Forward.
- No React/FastAPI migration; this adapts the ticket to the existing
  Streamlit UI.

## Governance

This is operator-facing presentation infrastructure only. It does not
select data, launch runs, change canonical runtime behavior, or reinterpret
artifact metrics.
