CS 271 Class Project
====================

This is a fully functional implementation of a distributed log system with Paxos as the underlying consensus protocol. 

You will need to create a 'config' file with the ip:port of all your servers. Add one line per server. A sample config file is provided which runs the servers on localhost. 

To run: python application.py <local ip> <local port> <global ip> <global port> [config]

<local ip>:<local port> is the ip address used by the message pump to listen to incoming messages. This is usually 127.0.0.1:XXXXX.
<global ip>:<global port> is usually the address which can be reached by any external server. 
config is the configuration file described earlier.

Type help in the prompt for a list of commands
