@echo off
echo === Deploy RootCoin to VPS ===
ssh root@100.83.87.98 "cd /home/rootcoin_app && git pull && source venv/bin/activate && pip install -r requirements.txt --quiet && sudo systemctl restart rootcoin && echo === Deploy complete ==="
pause
