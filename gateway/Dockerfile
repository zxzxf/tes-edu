FROM golang:1.22 AS build
WORKDIR /src
ENV GOPROXY=https://goproxy.cn,direct
ENV GOTOOLCHAIN=local
# 先复制全部源码再构建。go.sum 已随源码提交（勿在此处 go mod tidy：
# 现场 tidy 会掩盖 go.sum 缺失问题，只在 Docker 层“碰巧构建成功”，
# 而源码级 go build / CI 会失败——2026-10-21 已踩过此坑）。
COPY . .
RUN CGO_ENABLED=0 go build -o /gateway .

FROM alpine:3.20
RUN sed -i 's|https://dl-cdn.alpinelinux.org|https://mirrors.aliyun.com|g' /etc/apk/repositories \
 && apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=build /gateway /app/gateway
COPY config.yaml /app/config.yaml
ENV CONFIG_PATH=/app/config.yaml
ENV JWT_SECRET=""
EXPOSE 8080
ENTRYPOINT ["/app/gateway"]
