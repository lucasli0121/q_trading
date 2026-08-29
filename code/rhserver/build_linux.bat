set CGO_ENABLED=0
set GOOS=linux
set GOARCH=amd64
rem set GOARCH=arm
rem set GOARM=7
set GIN_MODE=release
go build -o rhserver main.go