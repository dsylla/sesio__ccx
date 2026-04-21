---
name: cognify
description: Trigger cognee knowledge graph processing. Checks status of running jobs and starts new cognify if idle. Use when user says "cognify", "process knowledge", "update graph", or when ~30min of data has accumulated.
tools: mcp__cognee__cognify, mcp__cognee__cognify_status
---

# Cognify — Process Knowledge Graph

Trigger cognee to process ingested data into the knowledge graph.

## Instructions

1. **Check current status** by calling `mcp__cognee__cognify_status`

2. **If a job is already running:**
   - Report: "Cognify is already running — [progress details]"
   - Do NOT start another job

3. **If idle (no active job):**
   - Call `mcp__cognee__cognify` with the data parameter set to a summary of what was ingested this session
   - Report: "Cognify started. Use /cognify again to check progress."

4. **If cognify fails:**
   - Report the error
   - Suggest checking the cognee-mcp container: `ssh pve 'pct exec 104 -- systemctl status cognee-mcp'`
