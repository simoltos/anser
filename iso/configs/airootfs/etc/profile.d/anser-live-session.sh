if [ "$(id -u)" = 0 ] && [ "$(tty 2>/dev/null)" = /dev/tty1 ]; then
    cd /root
    exec unshare --net -- /root/installer
fi
