/* Pumpkin's tool relay for Android (built as lib/<abi>/libpumpkin_mcp.so).
 *
 * llama-server starts tool servers as programs and talks to them over stdin/stdout. On Android Pumpkin's Python
 * lives inside the app and cannot be started as a program, so llama-server starts this instead:
 *
 *     libpumpkin_mcp.so <port> <server>        with PUMPKIN_MCP_KEY=<secret> in the environment
 *
 * It connects to Pumpkin on 127.0.0.1:<port>, says "<secret> <server>", and then copies stdin to Pumpkin and
 * Pumpkin's answers to stdout until either side closes. Pumpkin runs the tools (pumpkin/mcp_local.py). */
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include <arpa/inet.h>

static int write_all(int fd, const char *buf, size_t n) {
    while (n > 0) {
        ssize_t w = write(fd, buf, n);
        if (w < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        buf += w;
        n -= (size_t)w;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <port> <server>\n", argv[0]);
        return 2;
    }
    const char *key = getenv("PUMPKIN_MCP_KEY");
    if (!key || !*key) {
        fprintf(stderr, "PUMPKIN_MCP_KEY is not set\n");
        return 2;
    }
    int port = atoi(argv[1]);
    if (port <= 0 || port > 65535) {
        fprintf(stderr, "bad port %s\n", argv[1]);
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);

    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) { perror("socket"); return 1; }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((unsigned short)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(s, (struct sockaddr *)&addr, sizeof addr) < 0) {
        fprintf(stderr, "cannot reach Pumpkin on 127.0.0.1:%d: %s\n", port, strerror(errno));
        return 1;
    }
    if (write_all(s, key, strlen(key)) || write_all(s, " ", 1) ||
        write_all(s, argv[2], strlen(argv[2])) || write_all(s, "\n", 1)) {
        perror("write");
        return 1;
    }

    char buf[65536];
    struct pollfd fds[2] = {{.fd = 0, .events = POLLIN}, {.fd = s, .events = POLLIN}};
    int stdin_open = 1;
    for (;;) {
        int r = poll(fds, 2, -1);
        if (r < 0) {
            if (errno == EINTR) continue;
            return 1;
        }
        if (stdin_open && (fds[0].revents & (POLLIN | POLLHUP | POLLERR))) {
            ssize_t n = read(0, buf, sizeof buf);
            if (n > 0) {
                if (write_all(s, buf, (size_t)n)) return 0;
            } else if (n == 0 || errno != EINTR) {
                stdin_open = 0;                      /* llama-server is done: let Pumpkin finish and close */
                fds[0].fd = -1;
                shutdown(s, SHUT_WR);
            }
        }
        if (fds[1].revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t n = read(s, buf, sizeof buf);
            if (n > 0) {
                if (write_all(1, buf, (size_t)n)) return 0;
            } else if (n == 0 || errno != EINTR) {
                return 0;                            /* Pumpkin closed */
            }
        }
    }
}
