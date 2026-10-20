FROM golang:1.22 AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /gateway .

FROM gcr.io/distroless/static-debian12
COPY --from=build /gateway /gateway
COPY config.yaml /config.yaml
ENV JWT_SECRET=""
EXPOSE 8080
ENTRYPOINT ["/gateway"]
