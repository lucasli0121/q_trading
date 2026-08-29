###
 # @Author: liguoqiang
 # @Date: 2024-05-24 12:18:43
 # @LastEditors: liguoqiang
 # @LastEditTime: 2024-05-24 12:18:43
 # @Description: 
### 
export CGO_ENABLED=0
export GOOS=linux
export GOARCH=amd64
# export GOARCH=arm
# export GOARM=7
export GIN_MODE=release
go build -o rhserver main.go