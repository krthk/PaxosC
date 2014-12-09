#!/usr/bin/python

from sys import argv, exit
import threading
import time
import helper
import signal
import os
from paxos.node import Node
from paxos.log import Log


def signal_handler(sig, frame):
    print('You pressed Ctrl+C!')
    os.kill(os.getpid(), signal.SIGTERM)
    exit(0)

signal.signal(signal.SIGINT, signal_handler)

# Threading event for when our proposal is accepted
proposalCompleted = threading.Event()
proposalCompleted.set()

# Get the arguments
if len(argv) == 5:
    node = Node(argv[1], int(argv[2]), argv[3], int(argv[4]), proposalCompleted = proposalCompleted)
elif len(argv) == 6:
    node = Node(argv[1], int(argv[2]), argv[3], int(argv[4]), config = argv[5], proposalCompleted = proposalCompleted)

else:
    print ''
    print 'Usage: {0} <local ip> <local port> <global ip> <global port> [config]'.format(str(argv[0]))
    print ''
    exit(0)

# Create Node object
node.daemon = True
node.start()

# Wait a moment for the node to get its socket set up
time.sleep(1)

# Sync when you start
# node.logSync(node.log.transactions)

# Main loop of application
while True:
    # Wait for the current proposal to finish
    proposalCompleted.wait()
    
    # Get user input
    input = raw_input('\n> ')
    
    # End application
    if input == 'quit':
        exit(0)
    
    if input == 'help':
        print '\n------------------------------------------------\n'
        print '(b)alance'
        print '  - Returns the current balance\n'
        print '(d)eposit <amount>'
        print '  - Increments the current balance by <amount>\n'
        print '(w)ithdraw <amount>'
        print '  - Decrements the current balance by <amount>\n'
        print '(s)ync'
        print '  - Synchronizes the node log with the logs of the other servers\n'
        print '(f)ail'
        print '  - Simulates a node failure\n'
        print '(u)nfail'
        print '  - Starts node after fail was called\n'
        print '(p)rint'
        print '  - Prints the contents of the transaction log'
        print '------------------------------------------------'
        continue


    # Split the input into args
    args = input.split()

    if len(args) == 1:
        args[0] = args[0].lower()
        
        if args[0] == 'b' or args[0] == 'balance':
            print 'Balance: ', node.log.balance

        elif args[0] == 'f' or args[0] == 'fail':
            node.fail()
                
        elif args[0] == 'u' or args[0] == 'unfail':
            node.unfail()
            
        elif args[0] == 'p' or args[0] == 'print':
            node.log.history()
                
        elif args[0] == 's' or args[0] == 'sync':
            node.logSync(node.log.transactions)
                
    elif len(args) == 2:
        args[0] = args[0].lower()
        
        # Make sure second arg is a numerical value
        if helper.isNumber(args[1]):
            amount = float(args[1])
            h = hash((args[0], amount, node.addr, int(time.time())))
            
            if args[0] == 'd' or args[0] == 'deposit':
                proposalCompleted.clear()
                node.initPaxos(value = (Log.DEPOSIT, amount, h))
            
            elif args[0] == 'w' or args[0] == 'withdraw':
                if node.log.balance >= amount:
                    proposalCompleted.clear()
                    node.initPaxos(value = (Log.WITHDRAW, amount, h))
                else: 
                    print 'Not enough funds in your account. Sucker!'
    
        else:
            print 'Invalid amount'






