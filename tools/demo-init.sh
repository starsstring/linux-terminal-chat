#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts /run /tmp
mount -t devpts devpts /dev/pts
chmod 1777 /tmp
export TERM=xterm-256color LANG=C.UTF-8 HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
hostname chat-demo
ip link set lo up
stty cols 120 rows 36
cd /root/terminal-chat
tmux -f /dev/null new-session -d -s chat -x 120 -y 36 './chat server 8888; exec sh'
tmux split-window -h -t chat:0 './chat client 127.0.0.1 8888; exec sh'
tmux split-window -v -t chat:0.1 './chat client 127.0.0.1 8888; exec sh'
tmux set-option -g status-left 'Linux C Chat | '
tmux set-option -g status-right 'Ctrl+B then Arrow: switch pane | Ctrl+A then X: close VM'
tmux set-option -g status-interval 1
tmux select-pane -t chat:0.1
while true; do
    tmux attach-session -t chat
    echo 'Linux demo shell. Project: /root/terminal-chat. Run: python3 test_chat.py'
    /bin/sh
done
