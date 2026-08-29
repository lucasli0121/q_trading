#!/bin/bash


pid_val=0
get_pid() {
        s=`ps ax | grep q_data_proxy`
        echo $s
        if [ ${#s} == 0 ]; then
                return 0
        fi
        pid_val=`echo $s | cut -d ' ' -f1`
        return 1
}

get_pid
res=`echo $?`
if [ $res == 1 ]; then
	echo ready to kill $pid_val
        kill -9 $pid_val
fi
