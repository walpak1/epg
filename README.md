# XMLTV EPG for Singapore, Malaysia, and Indonesia

Public XMLTV guide data for selected TV markets in Singapore, Malaysia, and Indonesia.

This repository distributes **EPG/XML files only**. The private Python grabber code used to generate or update these files is **not included** here.

## Coverage

This repository focuses on XMLTV guide coverage for:

- Singapore
- Malaysia
- Indonesia


## What This Repository Contains

- XMLTV EPG files
- provider-specific guide output
- structured channel and programme listings

## What This Repository Does Not Contain

- Python source grabbers
- IPTV streams
- M3U playlists
- playback URLs
- DRM keys
- access or bypass tooling

If you are looking for stream delivery, playlist generation, or scraper source code, that is outside the scope of this public repository.

## Purpose

The goal of this repository is straightforward:

**publish XMLTV EPG data for supported providers and markets**

These files may be useful for:

- personal EPG usage
- XMLTV-compatible applications
- metadata testing
- schedule validation
- internal guide ingestion workflows

## Format

All guide files are provided in **XMLTV** format.

Depending on the source, coverage may include:

- channel identifiers
- display names
- programme titles
- programme descriptions
- programme start and stop times
- artwork when available

## Notes

- EPG completeness depends on the upstream source data.
- Some channels may have limited or missing schedule coverage on certain dates.
- Guide quality may vary by provider, market, and update window.
- File contents may change as source metadata changes.

## Scope Statement

This is an **EPG-only** repository.

It is intentionally separate from any private tooling used to generate the XML files, and it does not serve as an IPTV or streaming repository.

## Disclaimer

Use the published XML files only where you are authorized to access and process the related metadata. You are responsible for complying with provider terms and any applicable local rules or restrictions.
