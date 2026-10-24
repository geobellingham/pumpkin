---
name: review-security-logs
description: Triage security logs or alerts (Windows events, Sentinel or Defender exports, firewall, web server or auth logs) and say what happened, how bad it is and what to do. Use when the user pastes or attaches logs or alerts to investigate.
---

1. Work out what the data is: source, time range, time zone, and how many events. Say it in one line.
2. Look for what matters, roughly in this order:
   - Failed then successful sign-ins, sign-ins from new countries or at odd hours, impossible travel.
   - New accounts, group or role changes, password resets, MFA changes.
   - Processes started from unusual paths (temp, downloads, public), encoded PowerShell, LOLBins (certutil, mshta, rundll32, regsvr32), scheduled tasks and services created.
   - Outbound connections to rare destinations, large transfers, connections right after a suspicious process.
   - Log clearing, disabled security tools, tampering.
3. Build a short timeline of the suspicious events in time order, each with time, host, account and what happened.
4. List the indicators (IPs, domains, hashes, file paths, accounts) in a table.
5. Give a verdict: benign, suspicious or likely malicious, with how sure you are and why. Map to MITRE ATT&CK techniques where it clearly fits.
6. Give next steps in order: what to check to confirm, what to contain, and, when useful, a KQL query to hunt for more of the same in Microsoft Sentinel or Defender.
7. Never invent events that are not in the data. Say what is missing to be sure.
