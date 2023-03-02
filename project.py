"""
Where solution code to project should be written.  No other files should
be modified.

Matt Ely (811-653-312)
"""

import socket
import io
import time
import typing
import struct
import util
import util.logging


def send(sock: socket.socket, data: bytes):
    """
    Implementation of the sending logic for sending data over a slow,
    lossy, constrained network.

    Args:
        sock -- A socket object, constructed and initialized to communicate
                over a simulated lossy network.
        data -- A bytes object, containing the data to send over the network.
    """

    # Implementation of the send client on a server with a fixed packet window size of 2. 
    # Acknowledgements of packets being received allow the sequence number window to slide over.
    # Uses Go Back N to resend packets "lost" on the wire or ACKs "lost" on the wire.

    logger = util.logging.get_logger("project-sender")
    
    seq_num = 0
    # initial timeout value of .5 seconds and is constantly adjusted based on success/failure of packets
    timeout_val = 0.5
    sock.settimeout(timeout_val)
    # condition for sender to know whether to send any more packets or not
    is_still_sending = True

    while is_still_sending:
        chunk1 = get_packet(seq_num, data)
        chunk2 = chunk1
        # upon creating packets and sending them, the sending client will wait for ACK/NAK prior to sending next packets
        now_waiting = True
        # if there are more packets based on the sequence number, create packet 2 of the window
        if not is_last_packet(seq_num, data):
            chunk2 = get_packet(seq_num + 1, data)
        # executes on last packet send
        else: 
            while now_waiting:
                sock.send(chunk1)
                print('attemping to send last chunk') # debug msg
                try:
                    # often last packet is resent to server due to sequence number misalignment
                    # a naive approach is still used to cease communication
                    final_packet = sock.recv(util.MAX_PACKET)
                    final_ack, trash = split_packet(final_packet)
                    if is_last_packet(final_ack, data):
                        is_still_sending = False
                        now_waiting = False
                except socket.timeout:
                    print('now waiting on last packet') # debug msg
        # primary packet sending routine of sending 2 packets to wire and waiting on
        # proper acknowledgement before incrementing sequence number
        while now_waiting:
            sock.send(chunk1)
            # print("Sending packet 1 of window: " + str(seq_num)) # debug msg

            # initial time value taken to measure RTT
            time1 = time.time()
            sock.send(chunk2)
            # print("Sending packet 2 of window: " + str(seq_num + 1)) # debug msg

            # - two following try-catches await acknowledgement from server and only increment
            #   seq number if correct seq num is returned.
            # - if timeout is reached, seq num is not incremented and packets are
            #   sent again in hopes of successful transmission
            # - each ACK is checked to see if it is the last packet needing to be sent

            try:
                returned_ack = sock.recv(util.MAX_PACKET)
                # successful return from server indicates RTT and new timeout value is saved
                timeout_val = get_new_timeout(timeout_val, time.time() - time1)
                sock.settimeout(timeout_val)

                # print('new timeout value: ' + str(timeout_val)) # debug msg
                ack1, trash = split_packet(returned_ack)
                # print(f'recevied ack {ack1}') # debug msg

                seq_num = ack1 + 1
                now_waiting = False
                if is_last_packet(ack1, data):
                    is_still_sending = False
            except socket.timeout:
                print('incorrect ack # from chunk1')

            try:
                returned_ack = sock.recv(util.MAX_PACKET)
                ack2, trash = split_packet(returned_ack)
                print(f'recevied ack {ack2}')
                if seq_num == ack2:
                    seq_num = ack2 + 1
                now_waiting = False
                if is_last_packet(ack2, data):
                    is_still_sending = False
            except socket.timeout:
                print('incorrect ack # from chunk2')

            # print(f'SEQ NUM IS NOW {seq_num}') # debug msg
    


def recv(sock: socket.socket, dest: io.BufferedIOBase) -> int:
    """
    Implementation of the receiving logic for receiving data over a slow,
    lossy, constrained network.

    Args:
        sock -- A socket object, constructed and initialized to communicate
                over a simulated lossy network.

    Return:
        The number of bytes written to the destination.
    """

    # Implementation of receiver or server logic. This function expects packets over the
    # wire and returns the received packet's seq number as the ACK number.
    # If the incorrect packet is received (example being packet is dropped), then it will request
    # that packet be resent

    logger = util.logging.get_logger("project-receiver")
    
    # total number of bytes transmitted and return value of function
    num_bytes = 0
    # initially the receiver expects sequence number 0 to begin
    expected_seq_num = 0
    # is_same is used to determine if sender tries resending the same packet and potentially
    # the receiver is expecting the "wrong" sequence number. This edge case typically happens
    # when the last packet's ACK is dropped over the wire. If receiver ACKs packet 8 and now
    # expects packet 9, but packet 9 does not exist for the sender, the sender will keep sending
    # the last packet, packet 8. This fixes this annoying edge case and allows for a smooth finish
    is_same = False

    while True:
        data = sock.recv(util.MAX_PACKET)

        # if an empty packet is received, no more data is expected to be collect (naive approach)
        if not data:
            break

        # seq num and payload split from received packet
        seq_num, payload = split_packet(data)
       
        # print('recevied packet:' + str(seq_num)) # debug msg

        # if expected packet is received, it will write bytes to destination file
        if seq_num == expected_seq_num:
            # new packet is made with just 4-byte sequence number and no data,
            # this is the type of file the sender expects
            new_ack = make_packet(seq_num)
            sock.send(new_ack)

            # print('Correctly acked: ', seq_num) # debug msg

            expected_seq_num = expected_seq_num + 1
            # is_same flag set false to indicate not a duplicate packet
            is_same = False
            logger.info("Received %d bytes", len(payload))
            # bytes written to file destination
            dest.write(payload)
            # number of bytes received incremented
            num_bytes += len(payload)
            dest.flush()

        # incorrect sequence number received 
        else:
            # this condition indicates a packet is being resent because sender is probably
            # sending the last packet repeatedly and receiver is expecting the next one
            # this condition is NOT always executed on mismatch seq number
            if seq_num + 1 == expected_seq_num:
                is_same = True
            # this condition recognizes the repeated packet issue and sends ACK that sender is expecting
            # so that it stops resending the last packet
            if is_same:
                new_nak = make_packet(expected_seq_num - 1)
                sock.send(new_nak)

            # print(f'Just got packet {seq_num}, I want {expected_seq_num - 1} instead') # debug msg
    
    # function returns number of bytes received from sender
    return num_bytes

# get packet is how chunks of data are created with sequence number and data fed in
def get_packet(seq_num: int, data: bytes):
    # 4 bytes are allocated for seqence number, rest for data
    chunk_size = util.MAX_PACKET - 4
    offsets = range(0, len(data), chunk_size)
    k = 0
    # naive method of finding position in data to create next chunk
    for chunk in [data[i:i + chunk_size] for i in offsets]:
        if k == seq_num:
            # new chunk of data created
            new_chunk = make_packet(seq_num, chunk)
            return new_chunk
        else:
            k = k + 1

# packet creation called by get_packet to fill with seq number and bytes
# if no data arg is supplied, no bytes will be added in the data field
def make_packet(seq_num: int, data = b''):
    seq_num_to_bytes = seq_num.to_bytes(4, 'big')
    return seq_num_to_bytes + data

# splits the packet into a sequence number and data if there is data
# packets received have data, acknowledgements do not
def split_packet(packet: bytes):
    seq_num = int.from_bytes(packet[0:4], 'big')
    return seq_num, packet[4:]

# checks to see if current sequence number is the last packet in the file
def is_last_packet(seq_num: int, data: bytes):
    chunk_size = util.MAX_PACKET - 4
    offsets = range(0, len(data), chunk_size)
    return offsets[-1]/chunk_size == seq_num

# current timeout value and new RTT value supplied and their weights
# determine a new timeout time
# the timeout time shrinks as more packets are successfully transmitted
def get_new_timeout(current_timeout: float, new_timeout: float):
    return (current_timeout * .67) + (new_timeout * .5)
