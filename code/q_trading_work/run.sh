#!/bin/bash

cd /home/lucasli/q_trading_work_1

./kill_pid.sh

sleep 1s

rm -vf ./log/*

sleep 1s


(q_trading_work_1 &)
