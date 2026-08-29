#!/bin/bash

cd /media/lucasli/q_trading_cli

./kill_pid.sh

sleep 1s

rm -vf ./log/*

sleep 1s


(./q_trading_cli &)
