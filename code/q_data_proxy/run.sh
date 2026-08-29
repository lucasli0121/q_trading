#!/bin/bash

cd /home/lucasli/q_data_proxy

./kill_pid.sh

sleep 1s

rm -vf ./log/*

sleep 1s


(./q_data_proxy &)
