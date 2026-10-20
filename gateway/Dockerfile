FROM golang:1.22-alpine AS build
WORKDIR /src
# 复制全部源码（含 go.mod），用 go mod tidy 拉取依赖并生成 go.sum
COPY . .
RUN go mod tidy
RUN go build -o /bin/gateway .

FROM alpine:3.20
WORKDIR /app
COPY --from=build /bin/gateway /app/gateway
EXPOSE 8080
ENTRYPOINT ["/app/gateway"]
