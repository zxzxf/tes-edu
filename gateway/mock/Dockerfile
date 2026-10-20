FROM golang:1.22 AS build
WORKDIR /src
ENV GOTOOLCHAIN=local
COPY . .
RUN CGO_ENABLED=0 go build -o /mock .

FROM alpine:3.20
WORKDIR /app
COPY --from=build /mock /app/mock
EXPOSE 8000
ENTRYPOINT ["/app/mock"]
