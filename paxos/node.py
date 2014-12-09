#!/usr/bin/python

import sys
import socket
import threading
import thread
import time
import Queue
import pickle
import math
import random
from sets import Set
from messagepump import MessagePump
from paxosState import PaxosState
from paxosState import PaxosRole
from message import Message
from ballot import Ballot
from log import Log

class Node(threading.Thread):
    
    def __init__(self, localIP, localPort, globalIP, globalPort, config = 'config', proposalCompleted = None):
        threading.Thread.__init__(self)
        
        self.addr = (globalIP, globalPort)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Read config and add the servers to the set
        self.serverSet = Set()
        for server in open(config).read().splitlines():
            _ip, _port = server.split(':')
            
            # Only add if it is not the local server
            if _ip != self.addr[0] or int(_port) != self.addr[1]:
                self.serverSet.add((_ip, int(_port)))
        
        # Number of servers in the system including self
        self.numServers = len(self.serverSet) + 1
        
        # Compute the size of the majority quorum
        self.quorumSize = int(self.numServers/2)+1
        
        self.log = Log(localIP, localPort)
    
        # Use a set to maintain gaps with finished Paxos rounds. The next Paxos round will be the
        # smallest item in the set. If the set is empty, then it is highestRound
        self.setOfGaps = Set()
        self.highestRound = 0
        self.initSetOfGaps()
        
        self.paxosStates = {}
        
        self.lockValue = None
        
        self.hasFailed = False
        
        self.queue = Queue.Queue()
        self.msgReceived = threading.Event()
        self.msgReceived.clear()
        
        self.proposalCompleted = proposalCompleted
    
        self.messagePump = MessagePump(self.queue, self.msgReceived, owner = self, ip = localIP, port = localPort)
        self.messagePump.setDaemon(True)
    
    
    # Called when thread is started
    def run(self):
        # Get list of other servers
        self.messagePump.start()
        
        while True:
            self.msgReceived.wait()
            while not self.queue.empty():
                data, addr = self.queue.get()
                try:
                    msg = pickle.loads(data)
                    print '{0}: Received\n{1}'.format(self.addr, msg)
                    self.processMessage(msg, addr)
                except Exception as e:
                    print '{0}: {1}'.format(self.addr, data)
                    print '{0}: Exception with message\n{1}'.format(self.addr, msg)
                    print e
            self.msgReceived.clear()

    # Process the message msg received from the address addr
    def processMessage(self, msg, addr):
        # The round corresponding to the message
        r = msg.round

        # Check if it is a PROPOSE message
        if msg.messageType == Message.PROPOSER_PREPARE:
            # Check if we have already decided a value for this round
            if r in self.log.transactions:
                # Return a NACK and the value if this round has already been decided
                nack_msg = Message(msg.round, 
                                   Message.ACCEPTOR_NACK, 
                                   self.addr,
                                   msg.ballot, 
                                   {'decided': True, 'highestballot': None, 'value': self.log.transactions[r]})
                print '{0}: Sending a NACK to {1}'.format(self.addr, msg.source)
                self.sendMessage(nack_msg, msg.source)
                return
                     
            # Check if we already have sent/received a message for this round 
            if r in self.paxosStates:
                # Get the state corresponding to the current round
                state = self.paxosStates[r]
                
                # Respond to the proposer with a PROMISE not to accept any lower ballots
                if msg.ballot >= state.highestBallot:
                    promise_msg = Message(msg.round, 
                                          Message.ACCEPTOR_PROMISE, 
                                          self.addr,
                                          msg.ballot, 
                                          {'highestballot': state.highestBallot, 'value': state.value})
                    print '{0}: Sending PROMISE to {1}'.format(self.addr, msg.source)
                    self.sendMessage(promise_msg, msg.source)
                    
                    # Update the state corresponding to the current round
                    self.paxosStates[r] = PaxosState(r, PaxosRole.ACCEPTOR, 
                                                     PaxosState.ACCEPTOR_SENT_PROMISE, 
                                                     msg.ballot,
                                                     state.value)
                
                # Send a NACK message if we have already promised to a higher ballot
                else:
                    nack_msg = Message(msg.round, 
                                       Message.ACCEPTOR_NACK, 
                                       self.addr,
                                       msg.ballot, 
                                       {'highestballot': state.highestBallot, 'value': state.value})
                    print '{0}: Sending a NACK to {1}'.format(self.addr, msg.source)
                    self.sendMessage(nack_msg, msg.source)
            
            # We haven't touched this round yet. So, accept the proposal and send a PROMISE 
            else:
                # Respond to the proposer with a PROMISE not to accept any lower ballots
                promise_msg = Message(msg.round, 
                                      Message.ACCEPTOR_PROMISE,
                                      self.addr, 
                                      msg.ballot,
                                      {'highestballot': None, 'value': None})
                print '{0}: Sending PROMISE to {1}'.format(self.addr, msg.source)
                self.sendMessage(promise_msg, msg.source)
                
                # Update the state corresponding to the current round
                self.paxosStates[r] = PaxosState(r, PaxosRole.ACCEPTOR, 
                                                 PaxosState.ACCEPTOR_SENT_PROMISE,  
                                                 msg.ballot)

        elif msg.messageType == Message.ACCEPTOR_PROMISE:
            print '{0}: Received a PROMISE from {1}'.format(self.addr, msg.source)
            # Ensure we are the proposer for this round 
            if r not in self.paxosStates: return
            
            # Get the state corresponding to the current round
            state = self.paxosStates[r]

            # Return if I am not a proposer
            if state.role != PaxosRole.PROPOSER: return
            # Return if the PROMISE response is not for my current highest ballot
            if state.highestBallot != msg.ballot: return 
            
            if not state.responses:
                waitTime = 3
                timer = threading.Timer(waitTime, self.respondToPromises, [r])
                timer.start()

            # This is a valid PROMISE from one of the servers
            # Add this server to the set of positive responses 
            state.responses.append((msg.source, msg.metadata['highestballot'], msg.metadata['value']))

        
        elif msg.messageType == Message.ACCEPTOR_NACK:
            # If we receive a NACK indicating that the round has already been decided, update
            # our log and start a new round of Paxos for our original value
            if 'decided' in msg.metadata:
                if msg.round in self.log.transactions: 
                    return
                
                newState = PaxosState(r, PaxosRole.LEARNER, 
                                      PaxosState.LEARNER_DECIDED,  
                                      msg.metadata['highestballot'],
                                      msg.metadata['value'])
                self.paxosStates[r] = newState
                self.removeRound(r)
                self.initPaxos(value = self.lockValue)
                
                # Add the result to the log
                value_type, value_amount, value_hash = msg.metadata['value']
                self.log.addTransaction(r, value_type, value_amount, value_hash)
          
                return

            # If we receive a generic NACK for a state which we have not tracked, ignore
            if r not in self.paxosStates: 
                return 
            
            # Ignore if we receive a NACK for an earlier proposal
            if msg.ballot < self.paxosStates[r].highestBallot:
                return
            
            # If we have already processed an earlier NACK for the same (ballot,round), ignore this NACK
            if self.paxosStates[r].stage == PaxosState.PROPOSER_RECEIVED_NACK:
                return
            
            # If we receive a generic NACK message from any of the servers, abandon this round
            # because we are never going to succeed with the current ballot number
            self.paxosStates[r].stage = PaxosState.PROPOSER_RECEIVED_NACK

            waitTime = random.uniform(1.0, 5.0)
            timer = threading.Timer(waitTime, self.retryPaxos, [r, self.lockValue, msg.ballot])
            timer.start()
            print '{0}: Received NACK. Waiting {1} seconds and retrying'.format(self.addr, waitTime)
                
        elif msg.messageType == Message.PROPOSER_ACCEPT:
            # Try to get the state for the acceptor
            if r in self.paxosStates:
                state = self.paxosStates[r]
            else:
                return
            # Accept the ACCEPT request with the value if we haven't responded to any other 
            # server with a higher ballot
            if msg.ballot >= state.highestBallot:
                newState = PaxosState(r, PaxosRole.ACCEPTOR, 
                                      PaxosState.ACCEPTOR_ACCEPTED,  
                                      msg.ballot,
                                      msg.metadata['value'])
            
                print '{0}: Received ACCEPT message. Setting value to {1}'.format(self.addr, msg.metadata['value'])
                
                # Send ACCEPTOR_ACCEPT message to the proposer
                accepted_msg = Message(msg.round, 
                                       Message.ACCEPTOR_ACCEPT,
                                       self.addr,
                                       msg.ballot, 
                                       {'value': msg.metadata['value']})
                self.sendMessage(accepted_msg, msg.source)
                

            # If we received a newer proposal before getting an accept from the original proposer,
            # send a NACK to the original proposer
            else:
                nack_msg = Message(msg.round, 
                                   Message.ACCEPTOR_NACK, 
                                   self.addr,
                                   msg.ballot, 
                                   {'highestballot': state.highestBallot})
                print '{0}: Sending a NACK to {1}'.format(self.addr, msg.source)
                self.sendMessage(nack_msg, msg.source)

        elif msg.messageType == Message.ACCEPTOR_ACCEPT:
            print '{0}: Received an ACCEPT from {1}'.format(self.addr, msg.source)
            # Ensure we are the proposer for this round 
            if r not in self.paxosStates: return
            
            # Get the state corresponding to the current round
            state = self.paxosStates[r]

            # Return if I am not a proposer
            if state.role != PaxosRole.PROPOSER: return
            # Return if the ACCEPT response is not for my current highest ballot
            if state.highestBallot != msg.ballot: return 
            
            # Assert that the value accepted by the acceptor is the value proposed by the proposer
            assert msg.metadata['value'] == state.value
            
            # This is a valid ACCEPT from one of the servers
            # Add this server to the set of positive responses 
            state.responses.append(msg.source)
            
            # Check if we have a quorum. +1 to include ourself
            if len(state.responses) + 1 >= self.quorumSize:
                print '{0}: DECIDE Quorum formed'.format(self.addr)
                print '{0}: Sending DECIDE messages to all ACCEPTORS and LEARNERS'.format(self.addr)
                
                # Send DECIDE message to all the other servers
                decide_msg = Message(msg.round, 
                                     Message.PROPOSER_DECIDE,
                                     self.addr,
                                     state.highestBallot, 
                                     {'value': state.value})
                
                for server in self.serverSet:
                    self.sendMessage(decide_msg, server)

                # Update the state corresponding to sending the DECIDES
                newState = PaxosState(r, PaxosRole.PROPOSER, 
                                      PaxosState.PROPOSER_SENT_DECIDE,  
                                      state.highestBallot,
                                      state.value)
                self.paxosStates[r] = newState
                
                # Update the state to reflect that this round has been DECIDED
                self.removeRound(r)

                # Add the result to the log
                if isinstance(msg.metadata['value'], list):
                    value_type, value_amount, value_hash = self.getDecideValue(msg.metadata['value'])
                else:
                    value_type, value_amount, value_hash = msg.metadata['value']
                self.log.addTransaction(r, value_type, value_amount, value_hash)
          
                # If the value we just decided on is the value our user is waiting on, then we are done
                # Else, we need to start another round to get consensus on our original value
                if (value_type, value_amount, value_hash) == self.lockValue or self.lockValue in msg.metadata['value']:
                    self.proposalCompleted.set()
                else:
                    self.initPaxos(value = self.lockValue)
                

        elif msg.messageType == Message.PROPOSER_DECIDE:
            print '{0}: Received a DECIDE message'.format(self.addr)
            if r in self.paxosStates:
                # Get the state corresponding to the current round
                state = self.paxosStates[r]
    
                # Update the state corresponding to receiving the DECIDE
                newState = PaxosState(r, state.role, 
                                      PaxosState.ACCEPTOR_DECIDED if state.role == PaxosRole.ACCEPTOR else PaxosState.LEARNER_DECIDED,
                                      state.highestBallot,
                                      self.getDecideValue(msg.metadata['value']))
                self.paxosStates[r] = newState
            else:
                # Update the state corresponding to receiving the DECIDE
                newState = PaxosState(r, PaxosRole.LEARNER, 
                                      PaxosState.LEARNER_DECIDED,
                                      msg.ballot,
                                      self.getDecideValue(msg.metadata['value']))
                self.paxosStates[r] = newState
            
            # Update the state to reflect that this round has been DECIDED
            self.removeRound(r)
                
            # Add the result to the log
            if isinstance(msg.metadata['value'], list):
                value_type, value_amount, value_hash = self.getDecideValue(msg.metadata['value'])
            else:
                value_type, value_amount, value_hash = msg.metadata['value']
            self.log.addTransaction(r, value_type, value_amount, value_hash)

            # If some other proposer decided on our value, then release the application lock
            # Else, see if there is any state still tracking our original value. If not, start a fresh 
            # round of paxos for our original value
            if not self.lockValue: 
                return
            
            if (value_type, value_amount, value_hash) == self.lockValue or self.lockValue in msg.metadata['value']:
                self.proposalCompleted.set()
            else:
                for key in self.paxosStates:
                    if self.paxosStates[key].value == self.lockValue or self.lockValue in self.paxosStates[key].value:
                        return
                self.initPaxos(value = self.lockValue)

        elif msg.messageType == Message.LOG_SYNC_REQUEST:
            print '{0}: Received a SYNC REQUEST message from {1}'.format(self.addr, msg.source)
            msg_log = msg.metadata['log']
            response = {}
            for key in self.log.transactions:
                if key not in msg_log:
                    response[key] = self.log.transactions[key]
            if response:
                print '{0}: Sent a SYNC RESPONSE message to {1}'.format(self.addr, msg.source)
                self.logSync(response, msg.source, Message.LOG_SYNC_RESPONSE)
            
            for key in msg_log:
                if key not in self.log.transactions:
                    self.log.addTransaction(key, msg_log[key][0], msg_log[key][1], msg_log[key][2])
            
            # Don't forget to reinit the set of gaps
            self.initSetOfGaps()
                
        elif msg.messageType == Message.LOG_SYNC_RESPONSE:
            print '{0}: Received a SYNC RESPONSE message from {1}'.format(self.addr, msg.source)
            msg_log = msg.metadata['log']
            for key in msg_log:
                if key not in self.log.transactions:
                    self.log.addTransaction(key, msg_log[key][0], msg_log[key][1], msg_log[key][2])
            
            # Don't forget to reinit the set of gaps
            self.initSetOfGaps()
                
            

    # Initiate Paxos with a proposal to a quorum of servers
    def initPaxos(self, r = None, value = None, ballot = None):
        if r == None:
            r = self.getNextRound()
            
        if ballot == None:
            ballot = Ballot(self.addr[0], self.addr[1])
            if r in self.paxosStates:
                print '{0}: Found a previous ballot for this r. Setting current ballot greater than prev ballot.'.format(self.addr)
                ballot.set_n(self.paxosStates[r].highestBallot.n+1)

        self.lockValue = value

        prop_msg = Message(r, Message.PROPOSER_PREPARE, self.addr, ballot)
        
        print '{0}: Initiating Paxos for round {1}'.format(self.addr, r)
        self.paxosStates[r] = PaxosState(r, PaxosRole.PROPOSER, 
                                         PaxosState.PROPOSER_SENT_PROPOSAL,  
                                         ballot,
                                         value, 
                                         {'promise_quorum_servers':Set()})

        for server in self.serverSet:
            self.sendMessage(prop_msg, server)
            if 'promise_quorum_servers' in self.paxosStates[r].metadata:
                self.paxosStates[r].metadata['promise_quorum_servers'].add(server)
            else:
                self.paxosStates[r].metadata['promise_quorum_servers'] = Set([server])
                
#         t = threading.Thread(name='promise_thread', 
#                              target=self.extendPromiseQuorum, 
#                              args=[r, prop_msg, 5])
#         t.start()
        
    # Try more servers periodically if our initial attempt at getting a quorum was unsuccessful
    
    def extendPromiseQuorum(self, round, prop_msg, sleep_time = 5):
        while True:
            time.sleep(sleep_time)
            
            # The original paxos for this round failed/timed-out. Stop sending more promises for this round.
            if round not in self.paxosStates: 
                return
            
            # Return if the value gets decided by some other server
            if round in self.log.transactions: 
                return
            
            state = self.paxosStates[round]
            
            # If the proposer received a NACK and abandoned our corresponding round, we should also abandon
            if state.stage == PaxosState.PROPOSER_RECEIVED_NACK: 
                return
            
            if state.stage == PaxosState.PROPOSER_SENT_PROPOSAL:
                diff_set = self.serverSet - state.metadata['promise_quorum_servers']
                if not diff_set: return
                
                server = diff_set.pop()
                state.metadata['promise_quorum_servers'].add(server)

                print '{0}: Did not find a PROMISE quorum yet. Trying more servers for round {1}.'.format(self.addr, round)
                prop_msg.ballot = state.highestBallot
                self.sendMessage(prop_msg, server)
            else: 
                return
#                 thread.exit()

    def respondToPromises(self, r):
        state = self.paxosStates[r]
        
        nResponseSet = len(state.responses) + 1
        # Check if we have a quorum. +1 to include ourself
        if nResponseSet >= self.quorumSize:
            # Get the value corresponding to the highest ballot
            highestBallot, highestValue = None, None
            listOfValues = []
            for (_, ballot, value) in state.responses:
                if not ballot: continue
                if not highestBallot:
                    highestBallot, highestValue = ballot, value
                elif ballot > highestBallot:
                    highestBallot, highestValue = ballot, value
                
                if value: 
                    listOfValues.append(value)
            
            # Count the number of votes for the highest value, if we actually received anything but None
            maxVotes = 0
            if highestValue:
                assert listOfValues
                maxVotes = listOfValues.count(highestValue)
                
                if maxVotes + (self.numServers+1 - nResponseSet) < self.quorumSize:
                    newValue = [val for val in Set(listOfValues) if val[0] == self.lockValue[0]]
                    if newValue:
                        newValue.append(self.lockValue)
                        highestValue = newValue
            
            print '{0}: PROMISE Quorum formed'.format(self.addr)
            print '{0}: Sending ACCEPT messages to all ACCEPTORS'.format(self.addr)
            
            # If all the acceptors return None values, send ACCEPT messages with the value we are
            # trying to set. Else, set value to the highest value returned by the acceptors.
            if highestValue == None:
                highestValue = state.value
                
            accept_msg = Message(r, 
                                 Message.PROPOSER_ACCEPT,
                                 self.addr,
                                 state.highestBallot, 
                                 {'value': highestValue})
            
            for (source, _, _) in self.paxosStates[r].responses:
                self.sendMessage(accept_msg, source)

            # Update the state corresponding to sending the accepts
            newState = PaxosState(r, PaxosRole.PROPOSER, 
                                  PaxosState.PROPOSER_SENT_ACCEPT,  
                                  state.highestBallot,
                                  highestValue)
            self.paxosStates[r] = newState

    def getDecideValue(self, listVals):
        if not isinstance(listVals, list): 
            return listVals
        
        z = zip(*listVals)
        assert len(Set(z[0])) == 1
        return (z[0][0], sum(z[1]), hash(z[2]))
        
    #After receiving a NACK, retry with the lowest available round and the failed value
    def retryPaxos(self, round, failedValue, highestBallot):
#         newRound = self.getNextRound()
        ballot = Ballot(self.addr[0], self.addr[1], highestBallot.n+1)
        print '{0}: Retrying round {1} with new ballot {2}'.format(self.addr, round, ballot)
        self.initPaxos(round, failedValue, ballot)
    
    # Get the next available round number 
    def getNextRound(self):
        if not self.setOfGaps: 
            return self.highestRound
        else:
            return min(self.setOfGaps)
        
    # Update the rounds when a DECIDE has been made
    def removeRound(self, r):
        if r in self.setOfGaps: 
            self.setOfGaps.remove(r)
        elif r == self.highestRound:
            self.highestRound += 1
        else:
            for i in xrange(self.highestRound, r):
                self.setOfGaps.add(i)
                self.highestRound = r+1
    
    # Returns a list of servers other than self that create a quorum
    def getQuorum(self):
        return random.sample(self.serverSet, self.quorumSize-1)
    
    # Serialize and send the given message msg to the given address addr
    def sendMessage(self, msg, addr):
        if self.hasFailed: 
            return
        
        print '{0}: Sent a message to {1}'.format(self.addr, addr)
        data = pickle.dumps(msg)
#         time.sleep(random.uniform(0.0, 1.0))
        self.socket.sendto(data, addr)
    
    def initSetOfGaps(self):
        if not self.log.transactions: return
        
        rounds_decided = sorted(iter(self.log.transactions))
        self.highestRound = rounds_decided[-1] + 1
        
        self.setOfGaps |= Set(xrange(rounds_decided[0]))
        for i in xrange(len(rounds_decided)-1):
            self.setOfGaps |= Set(xrange(rounds_decided[i]+1, rounds_decided[i+1]))
            
    def logSync(self, log, addr = None, messageType = Message.LOG_SYNC_REQUEST):
        log_msg = Message(None, 
                          messageType,
                          self.addr,
                          None, 
                          {'log': log})
        
        if addr:
            self.sendMessage(log_msg, addr)
        else:
            for server in self.serverSet:
                self.sendMessage(log_msg, server)

    # Stop all network activity
    def fail(self):
        assert self.hasFailed != self.messagePump.isRunning
        
        if self.hasFailed:
            print '{0}: Already failed'.format(self.addr)
        
        else:
            self.messagePump.isRunning = False
            self.hasFailed = True
            print '{0}: Halting activity'.format(self.addr)

    # Resume network activity
    def unfail(self):
        assert self.hasFailed != self.messagePump.isRunning

        if not self.hasFailed:
            print '{0}: Already running'.format(self.addr)
        
        else:
            self.messagePump.isRunning = True
            self.hasFailed = False
            print '{0}: Resuming activity'.format(self.addr)

if __name__ == '__main__':
    n1 = Node('127.0.0.1', 55555, '127.0.0.1', 55555, '../config2')
    print n1.getDecideValue([(1,2,23), (1,3,45)])
#     n1.start()
# 
#     n2 = Node('127.0.0.1', 55556, 'config2')
#     n2.start()
#     
#     n3 = Node('127.0.0.1', 55557, 'config2')
#     n3.start()
# 
#     n4 = Node('127.0.0.1', 55558, 'config2')
#     n4.start()
# 
#     time.sleep(2)
#     n3.initPaxos(0, value = 10)
#     time.sleep(5)
#     print n1.paxosStates[0]
#     print n2.paxosStates[0]
#     print n3.paxosStates[0]
#     print n4.paxosStates[0]
#     
#     n1.removeRound(4)
#     print n1.setOfGaps
#     print n1.getNextRound()
