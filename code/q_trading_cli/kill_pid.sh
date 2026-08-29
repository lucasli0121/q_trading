#!/bin/bash

pid_val=0
get_pid() {
        s=`ps -e | grep q_trading_cli`
        echo $s
        if [ ${#s} == 0 ]; then
                return 0
        fi
        pid_val=`echo $s | cut -d ' ' -f1`
        return 1
}

get_pid
res=`echo $?`
while [ $res == 1 ]
do
        echo ready to kill $pid_val
        kill $pid_val
	sleep 1s
        get_pid
        res=`echo $?`
done
