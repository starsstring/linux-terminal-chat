/* Linux terminal chat: one server, several clients. */
#include <arpa/inet.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#define USERS 16
#define LINE 1024

struct peer {
    int fd;
    unsigned long id;
    size_t used;
    char line[LINE];
};

/* TCP may send fewer bytes than requested, so finish in a loop. */
static int send_all(int fd, const char *buf, size_t len)
{
    while (len) {
        ssize_t n = send(fd, buf, len, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        buf += n;
        len -= (size_t)n;
    }
    return 0;
}

static void drop(struct peer *p)
{
    close(p->fd);
    p->fd = -1;
    p->used = 0;
}

static void broadcast(struct peer peers[], const char *msg)
{
    for (int i = 0; i < USERS; ++i)
        if (peers[i].fd >= 0 &&
            send_all(peers[i].fd, msg, strlen(msg)) < 0)
            drop(&peers[i]);
}

static int server(unsigned short port)
{
    struct peer peers[USERS];
    unsigned long next_id = 1;
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    int yes = 1;
    struct sockaddr_in addr = {0};
    if (listener < 0) { perror("socket"); return 1; }
    if (listener >= FD_SETSIZE) { close(listener); return 1; }
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(listener, (struct sockaddr *)&addr, sizeof addr) < 0 ||
        listen(listener, USERS) < 0) {
        perror("bind/listen"); close(listener); return 1;
    }
    for (int i = 0; i < USERS; ++i) {
        memset(&peers[i], 0, sizeof peers[i]);
        peers[i].fd = -1;
    }
    printf("Server listening on port %u. Ctrl+C to stop.\n", port);
    fflush(stdout);
    for (;;) {
        fd_set reads;
        int maxfd = listener;
        FD_ZERO(&reads);
        FD_SET(listener, &reads);
        for (int i = 0; i < USERS; ++i) {
            if (peers[i].fd < 0) continue;
            FD_SET(peers[i].fd, &reads);
            if (peers[i].fd > maxfd) maxfd = peers[i].fd;
        }
        if (select(maxfd + 1, &reads, NULL, NULL, NULL) < 0) {
            if (errno == EINTR) continue;
            perror("select"); break;
        }
        /* Process existing sockets before accepting new ones. */
        for (int i = 0; i < USERS; ++i) {
            struct peer *p = &peers[i];
            char input[512], out[LINE + 96];
            if (p->fd < 0 || !FD_ISSET(p->fd, &reads)) continue;
            ssize_t n = recv(p->fd, input, sizeof input, 0);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) {
                snprintf(out, sizeof out, "[system] User %lu left.\n", p->id);
                drop(p);
                broadcast(peers, out);
                continue;
            }
            /* Newline frames messages across split/coalesced TCP reads. */
            for (ssize_t j = 0; j < n && p->fd >= 0; ++j) {
                unsigned char ch = (unsigned char)input[j];
                if (ch == '\n') {
                    p->line[p->used] = '\0';
                    if (!strcmp(p->line, "/quit")) {
                        snprintf(out, sizeof out,
                                 "[system] User %lu left.\n", p->id);
                        drop(p);
                        broadcast(peers, out);
                        break;
                    }
                    if (p->used) {
                        snprintf(out, sizeof out, "[User %lu] %s\n", p->id, p->line);
                        p->used = 0;
                        broadcast(peers, out);
                    }
                } else if (ch == '\r') {
                    continue;
                } else if (ch < 32 || ch == 127 || p->used == LINE - 1) {
                    const char *error = "[system] Invalid or too long message.\n";
                    send_all(p->fd, error, strlen(error));
                    drop(p);
                } else {
                    p->line[p->used++] = (char)ch;
                }
            }
        }
        if (FD_ISSET(listener, &reads)) {
            int fd = accept(listener, NULL, NULL);
            if (fd < 0) { perror("accept"); continue; }
            int slot;
            for (slot = 0; slot < USERS && peers[slot].fd >= 0; ++slot) {}
            if (slot == USERS || fd >= FD_SETSIZE) {
                close(fd); continue;
            }
            /* Bound slow-reader stalls in this small teaching server. */
            struct timeval timeout = {2, 0};
            if (setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO,
                           &timeout, sizeof timeout) < 0) {
                perror("setsockopt"); close(fd); continue;
            }
            peers[slot].fd = fd;
            peers[slot].id = next_id++;
            peers[slot].used = 0;
            char welcome[160];
            snprintf(welcome, sizeof welcome,
                     "[system] You are User %lu. Enter text; /quit to leave.\n",
                     peers[slot].id);
            if (send_all(fd, welcome, strlen(welcome)) < 0) {
                drop(&peers[slot]); continue;
            }
            snprintf(welcome, sizeof welcome, "[system] User %lu joined.\n",
                     peers[slot].id);
            broadcast(peers, welcome);
        }
    }
    for (int i = 0; i < USERS; ++i)
        if (peers[i].fd >= 0) drop(&peers[i]);
    close(listener);
    return 1;
}

static int client(const char *ip, unsigned short port)
{
    struct sockaddr_in addr = {0};
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    int result = 0;
    if (fd < 0) { perror("socket"); return 1; }
    if (fd >= FD_SETSIZE) { close(fd); return 1; }
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) != 1) {
        fprintf(stderr, "Use an IPv4 address, e.g. 127.0.0.1\n");
        close(fd); return 1;
    }
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        perror("connect"); close(fd); return 1;
    }
    for (;;) {
        fd_set reads;
        FD_ZERO(&reads);
        FD_SET(fd, &reads);
        FD_SET(STDIN_FILENO, &reads);
        if (select(fd + 1, &reads, NULL, NULL, NULL) < 0) {
            if (errno == EINTR) continue;
            perror("select"); result = 1; break;
        }
        char buf[LINE];
        if (FD_ISSET(fd, &reads)) {
            ssize_t n = recv(fd, buf, sizeof buf, 0);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) {
                puts("[system] Connection closed.");
                if (n < 0) { perror("recv"); result = 1; }
                break;
            }
            fwrite(buf, 1, (size_t)n, stdout);
            fflush(stdout);
        }
        if (FD_ISSET(STDIN_FILENO, &reads)) {
            ssize_t n = read(STDIN_FILENO, buf, sizeof buf);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) {
                if (n < 0) { perror("read"); result = 1; }
                break;
            }
            if (send_all(fd, buf, (size_t)n) < 0) {
                perror("send"); result = 1; break;
            }
        }
    }
    close(fd);
    return result;
}

int main(int argc, char **argv)
{
    int is_server = argc == 3 && !strcmp(argv[1], "server");
    int is_client = argc == 4 && !strcmp(argv[1], "client");
    if (!is_server && !is_client) {
        fprintf(stderr, "Usage:\n  %s server PORT\n  %s client IPv4 PORT\n",
                argv[0], argv[0]);
        return 1;
    }
    char *end;
    errno = 0;
    long port = strtol(argv[argc - 1], &end, 10);
    if (errno || !argv[argc - 1][0] || *end || port < 1 || port > 65535) {
        fprintf(stderr, "Port must be 1..65535\n"); return 1;
    }
    signal(SIGPIPE, SIG_IGN);
    return is_server ? server((unsigned short)port)
                     : client(argv[2], (unsigned short)port);
}
