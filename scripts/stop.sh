#!/usr/bin/env bash
# Stop a running GuudFlow dev stack: the API (:8000) and the Studio dev server (:3001).
# Works without lsof (WSL often lacks it) by matching process names, then checking the ports.
pkill -f 'uvicorn.*api\.main' 2>/dev/null && echo "stopped the API"
pkill -f 'studio.*vite|node.*vite' 2>/dev/null && echo "stopped the Studio dev server"
sleep 0.5
for port in 8000 3001 3002 3003; do   # 3002+: where vite lands when 3001 was taken earlier
  pids=$( (command -v lsof >/dev/null 2>&1 && lsof -ti tcp:"$port") || \
          (ss -ltnp 2>/dev/null | grep ":${port} " | grep -o 'pid=[0-9]*' | cut -d= -f2) || true)
  [ -n "${pids:-}" ] && { kill -9 $pids 2>/dev/null && echo "killed pid(s) $pids on :$port"; }
done
sleep 0.3
for port in 8000 3001; do
  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${port} "; then
    echo "port ${port} is still in use:"; ss -ltnp 2>/dev/null | grep ":${port} " | head -2
  fi
done
echo "done"
