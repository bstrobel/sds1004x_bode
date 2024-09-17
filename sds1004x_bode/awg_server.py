'''
Created on Apr 14, 2018

@author: 4x1md, hb020


This file contains the classes for the rpcbind port mapper (on TCP and UDP) and the VXI-11 listener.

It starts the port mappers as separate processes and runs the VXI-11 loop in the main process.

The port changes of the VXI-11 loop are communicated to the port mappers via a shared variable (with locking).

'''

import multiprocessing
import socket
from awgdrivers.base_awg import BaseAWG
from command_parser import CommandParser

# Host and ports to use.
# Setting host to 0.0.0.0 will bind the incoming connections to any interface.
#  PRCBIND port should always remain 111.
#  VXI-11 port can be changed to another value.
HOST = '0.0.0.0'
RPCBIND_PORT = 111
VXI11_PORTRANGE_START = 9010
VXI11_PORTRANGE_END = 9019


# AWG ID to send to the oscilloscope
#  Examples: SDG SDG2042X SDG0000X SDG2000X
#  The ID should begin with SDG letters.
AWG_ID_STRING = b"IDN-SGLT-PRI SDG0000X"

# RPC/VXI-11 procedure ids
GET_PORT = 3
CREATE_LINK = 10
DEVICE_WRITE = 11
DEVICE_READ = 12
DESTROY_LINK = 23
LXI_PROCEDURES = {
    10: "CREATE_LINK",
    11: "DEVICE_WRITE",
    12: "DEVICE_READ",
    23: "DESTROY_LINK"
}

# VXI-11 Core (395183)
VXI11_CORE_ID = 395183
# Function responses
NOT_VXI11_ERROR = -1
NOT_GET_PORT_ERROR = -2
UNKNOWN_COMMAND_ERROR = -4
OK = 0


class CommsObject(object):
    """
    Base class for the network interactions
    """
    
    def create_socket(self, host: str, port: int, on_udp: bool, myname: str):
        """Create a UDP or TCP socket, and starts listening

        :param host: host
        :type host: str
        :param port: port
        :type port: int
        :param on_udp: True for UDP
        :type on_udp: bool
        :param myname: the name to use in error messages
        :type myname: str
        :return: the socket
        :rtype: socket.socket
        """
        if on_udp:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((host, port))
            except OSError as ex:
                print(f"{myname}: Fatal error: {ex}. Cannot open UDP port {port} on address {host} for listening.")
                exit(1)
        else:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Disable the TIME_WAIT state of connected sockets, and allow reuse, as I switch ports rather quickly
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                sock.listen(1)  # Become a server socket, maximum 1 connection
            except OSError as ex:
                print(f"{myname}: Fatal error: {ex}. Cannot open TCP port {port} on address {host} for listening.")
                exit(1)
        return sock
        
    def get_xid(self, rx_packet):
        """
        Extracts XID from the incoming RPC packet.
        """
        xid = rx_packet[0x00:0x04]
        return xid
    
    def generate_packet_size_header(self, size):
        """
        Generates the header containing reply packet size.
        """
        # 1... .... .... .... .... .... .... .... = Last Fragment: Yes
        size = size | 0x80000000
        # .000 0000 0000 0000 0000 0000 0001 1100 = Fragment Length: 28
        res = self.uint_to_bytes(size)
        return res
    
    def generate_resp_data(self, xid, resp, on_udp=False):
        """
        Generates the response data to be sent to the oscilloscope.
        """
        # Generate RPC header
        rpc_hdr = self.generate_rpc_header(xid)
        if on_udp:
            # Merge all the headers
            resp_data = rpc_hdr + resp
        else:
            # Generate packet size header
            data_size = len(rpc_hdr) + len(resp)
            size_hdr = self.generate_packet_size_header(data_size)
            # Merge all the headers
            # debug: print(f"size_hdr: [{' '.join(format(x, '02x') for x in size_hdr)}]")
            # debug: print(f"rpc_hdr: [{' '.join(format(x, '02x') for x in rpc_hdr)}]")
            # debug: print(f"resp: [{' '.join(format(x, '02x') for x in resp)}]")
            resp_data = size_hdr + rpc_hdr + resp
        return resp_data    

    def generate_rpc_header(self, xid):
        """
        Generates RPC header for replying to oscilloscope's requests.
        @param xid: XID from the request packet as bytes sequence.
        """

        hdr = b""

        # XID: 0xXXXXXXXX (4 bytes)
        hdr += xid
        # Message Type: Reply (1)
        hdr += b"\x00\x00\x00\x01"
        # Reply State: accepted (0)
        hdr += b"\x00\x00\x00\x00"
        # Verifier
        #  Flavor: AUTH_NULL (0)
        hdr += b"\x00\x00\x00\x00"
        #  Length: 0
        hdr += b"\x00\x00\x00\x00"
        # Accept State: RPC executed successfully (0)
        hdr += b"\x00\x00\x00\x00"
        return hdr

    # =========================================================================
    #   Helper functions
    # =========================================================================

    def bytes_to_uint(self, bytes_seq):
        """
        Converts a sequence of 4 bytes to 32-bit integer. Byte 0 is MSB.
        """
        return int.from_bytes(bytes_seq, "big")
        # num = ord(bytes_seq[0])
        # num = num * 0x100 + ord(bytes_seq[1])
        # num = num * 0x100 + ord(bytes_seq[2])
        # num = num * 0x100 + ord(bytes_seq[3])
        # return num

    def uint_to_bytes(self, num):
        """
        Converts a 32-bit integer to a sequence of 4 bytes. Byte 0 is MSB.
        """
        return num.to_bytes(4, "big")
        # byte3 = (num / 0x1000000) & 0xFF
        # byte2 = (num / 0x10000) & 0xFF
        # byte1 = (num / 0x100) & 0xFF
        # byte0 = num & 0xFF
        # bytes_seq = bytearray((byte3, byte2, byte1, byte0))
        # return bytes_seq

    def print_as_hex(self, buf):
        """
        Prints a buffer as a set of hexadecimal numbers.
        Created for debug purposes.
        """
        buf_str = ""
        for b in buf:
            buf_str += "0x%X " % ord(b)
        print(buf_str)
            

class Portmapper(CommsObject, multiprocessing.Process):
    """Port mapper "thread" class
    """
    
    def __init__(self, host: str, rpcbind_port: int, on_udp: bool, vxi11_port):
        """init the port mapper.
        It will start the portmapper in a separate process.

        :param host: host
        :type host: str
        :param rpcbind_port: port
        :type rpcbind_port: int
        :param on_udp: True for UDP
        :type on_udp: bool
        :param vxi11_port: the port used by the VXI-11 service
        :type vxi11_port: multiprocessing.Value
        """
        multiprocessing.Process.__init__(self)
        self.exit = multiprocessing.Event()

        if host is not None:
            self.host = host
        else:
            self.host = HOST

        if not isinstance(rpcbind_port, (int, type(None))):
            raise TypeError("rpcbind_port must be an integer.")
        if rpcbind_port is not None:
            self.rpcbind_port = rpcbind_port
        else:
            self.rpcbind_port = RPCBIND_PORT
        self.on_udp = on_udp
        self.vxi11_port = vxi11_port
        self.myname = f"{'UDP' if on_udp else 'TCP'}Portmapper"
        
    def run(self):
        """
        Run the main loop of the mapper
        """
        try:
            # Create RPCBIND socket
            self.rpcbind_socket = self.create_socket(self.host, self.rpcbind_port, self.on_udp, self.myname)
            
            while not self.exit.is_set():
                # VXI-11 requests are processed after receiving a valid RPCBIND request.
                # print(f"{self.myname}: Waiting for connection request...")
                if self.on_udp:
                    res = self.process_rpcbind_request_udp()
                else:
                    res = self.process_rpcbind_request_tcp()
                if res != OK:
                    print("Incompatible RPCBIND request.")
        except KeyboardInterrupt:
            pass                
        # print(f"{self.myname}: shut down")
    
    def terminate(self):
        # not used normally, just in case the awgserver shuts down
        print(f"{self.myname}: terminate()")
        self.exit.set()
           
    def process_rpcbind_request_tcp(self):
        """Replies to RPCBIND/Portmap request and sends VXI-11 port number to the oscilloscope."""
        # RFC 1057 and RFC 1833 apply here. The scope uses V2, so RFC 1057 suffices.

        connection, address = self.rpcbind_socket.accept()
        rx_data = connection.recv(128)
        if len(rx_data) > 4:
            rx_data = rx_data[0x04:]  # start from XID, as with UDP
            print(f"{self.myname}: Incoming connection from {address[0]}:{address[1]}.")
            # Validate the request.
            #  If the request is not GETPORT or does not come from VXI-11 Core (395183),
            #  we have nothing to do wit it
            procedure = self.get_procedure(rx_data)
            if procedure != GET_PORT:
                return NOT_GET_PORT_ERROR
            program_id = self.get_program_id(rx_data)
            if program_id != VXI11_CORE_ID:
                return NOT_VXI11_ERROR
            # Generate and send response
            resp = self.generate_rpcbind_response()
            xid = self.get_xid(rx_data)
            resp_data = self.generate_resp_data(xid, resp, False)
            connection.send(resp_data)
        # Close connection and RPCBIND socket.
        connection.close()
        return OK
    
    def process_rpcbind_request_udp(self):
        """Replies to RPCBIND/Portmap request and sends VXI-11 port number to the oscilloscope."""
        # RFC 1057 and RFC 1833 apply here. The scope uses V2, so RFC 1057 suffices.
        
        bufferSize = 1024
        bytesAddressPair = self.rpcbind_socket.recvfrom(bufferSize)

        rx_data = bytesAddressPair[0]
        address = bytesAddressPair[1]

        if len(rx_data) > 0:
            print(f"{self.myname}: Incoming connection from {address[0]}:{address[1]}.")
            # Validate the request.
            #  If the request is not GETPORT or does not come from VXI-11 Core (395183),
            #  we have nothing to do wit it
            # debug: print(f"Data: [{' '.join(format(x, '02x') for x in rx_data)}]")
            #  This should be: 
            #  ....XID.... 00 00 00 00  00 00 00 02 00 01 86 a0 
            #  00 00 00 02 00 00 00 03  00 00 00 00 00 00 00 00 
            #  00 00 00 00 00 00 00 00  00 06 07 af 00 00 00 01 
            #  00 00 00 06 00 00 00 00
            procedure = self.get_procedure(rx_data)
            if procedure != GET_PORT:
                return NOT_GET_PORT_ERROR
            program_id = self.get_program_id(rx_data)
            if program_id != VXI11_CORE_ID:
                return NOT_VXI11_ERROR
            # Generate and send response
            resp = self.generate_rpcbind_response()
            xid = self.get_xid(rx_data)
            # debug: print(f"XID: [{' '.join(format(x, '02x') for x in xid)}]")
            resp_data = self.generate_resp_data(xid, resp, True)
            # debug: print(f"resp_data: [{' '.join(format(x, '02x') for x in resp_data)}]")
            self.rpcbind_socket.sendto(resp_data, address)

        return OK    
    
    def generate_rpcbind_response(self):
        """Returns VXI-11 port number as response to RPCBIND request."""
        # self.vxi11_port is a multiprocessing.Value object, that is a ctypes object in shared memory
        # that is synchronized using RLock. So it always gets the latest value.
        myport = self.vxi11_port.value
        print(f"{self.myname}: Sending to TCP port {myport}")
        resp = self.uint_to_bytes(myport)
        return resp
    
    def get_procedure(self, rx_packet):
        """
        Extracts procedure from the incoming RPC packet.
        """
        return self.bytes_to_uint(rx_packet[0x14:0x18])

    def get_program_id(self, rx_packet):
        """
        Extracts program_id from the incoming RPC packet.
        """
        return self.bytes_to_uint(rx_packet[0x28:0x2C])
        
    def close_socket(self):
        """
        Closes RPCBIND socket.
        """
        try:
            self.rpcbind_socket.close()
        except:
            pass

    def __del__(self):
        self.close_socket()    
    

class AwgServer(CommsObject):

    def __init__(self, awg, host=None, rpcbind_port=None, vxi11_portrange_start=None, vxi11_portrange_end=None):
        if host is not None:
            self.host = host
        else:
            self.host = HOST

        if not isinstance(rpcbind_port, (int, type(None))):
            raise TypeError("rpcbind_port must be an integer.")
        if rpcbind_port is not None:
            self.rpcbind_port = rpcbind_port
        else:
            self.rpcbind_port = RPCBIND_PORT

        if not isinstance(vxi11_portrange_start, (int, type(None))):
            raise TypeError("vxi11_port range start must be an integer.")
        if vxi11_portrange_start is not None:
            self.vxi11_portrange_start = vxi11_portrange_start
        else:
            self.vxi11_portrange_start = VXI11_PORTRANGE_START

        if not isinstance(vxi11_portrange_end, (int, type(None))):
            raise TypeError("vxi11_port range start must be an integer.")
        if vxi11_portrange_end is not None:
            self.vxi11_portrange_end = vxi11_portrange_end
        else:
            self.vxi11_portrange_end = VXI11_PORTRANGE_END

        self.vxi11_port = multiprocessing.Value('I', self.vxi11_portrange_start)
            
        if awg is None or not isinstance(awg, BaseAWG):
            raise TypeError("awg variable must be of AWG class.")
        self.awg = awg
        self.myname = "VXI-11"
        self.pm1 = None
        self.pm2 = None
            
    def start(self):
        """
        Makes all required initializations and starts the server.
        """

        print("Starting AWG server...")
        
        print(f"Portmapper: Listening to UDP and TCP ports on {self.host}:{self.rpcbind_port}")
        self.pm1 = Portmapper(self.host, self.rpcbind_port, True, self.vxi11_port).start()
        self.pm2 = Portmapper(self.host, self.rpcbind_port, False, self.vxi11_port).start()
        # Create VXI-11 socket
        print(f"{self.myname}: Listening to TCP port {self.host}:{self.vxi11_port.value}")
        self.lxi_socket = self.create_socket(self.host, self.vxi11_port.value, False, self.myname)

        # Initialize SCPI command parser
        self.parser = CommandParser(self.awg)

        # Connect to the external AWG
        # self.awg.initialize()

        # Run the server
        self.main_loop()

    def main_loop(self):
        """
        The main loop of the server.
        """

        # Run the VXI-11 server
        while True:
            # print("Waiting for LXI request.")
            self.process_lxi_requests()
            
            # every request must go to a new socket (as SDS800X-HD requires)
            self.close_lxi_sockets()
            self.vxi11_port.value += 1
            if self.vxi11_port.value > self.vxi11_portrange_end:
                self.vxi11_port.value = self.vxi11_portrange_start
            print(f"{self.myname}: moving to TCP port {self.vxi11_port.value}")
            self.lxi_socket = self.create_socket(self.host, self.vxi11_port.value, False, self.myname)
            
        # This code will never be reached
        # Disconnect from the external AWG
        self.signal_gen.connect()

    def process_lxi_requests(self):
        connection, address = self.lxi_socket.accept()
        while True:
            rx_buf = connection.recv(255)
            if len(rx_buf) > 0:
                resp = b''  # default
                
                # Parse incoming VXI-11 command
                status, vxi11_procedure, scpi_command, cmd_length = self.parse_lxi_request(rx_buf)

                if status == NOT_VXI11_ERROR:
                    print("Received VXI-11 request from an unknown source.")
                    break
                elif status == UNKNOWN_COMMAND_ERROR:
                    print("Unknown VXI-11 request received. Procedure id %s" % (vxi11_procedure))
                    break

                print("VXI-11 %s, SCPI command: %s" % (LXI_PROCEDURES[vxi11_procedure], scpi_command))

                # Process the received VXI-11 request
                if vxi11_procedure == CREATE_LINK:
                    resp = self.generate_lxi_create_link_response()

                elif vxi11_procedure == DEVICE_WRITE:
                    """
                    The parser parses and executes the received SCPI command.
                    VXI-11 DEVICE_WRITE function requires an empty reply.
                    """
                    self.parser.parse_scpi_command(scpi_command)
                    resp = self.generate_lxi_device_write_response(cmd_length)

                elif vxi11_procedure == DEVICE_READ:
                    """
                    DEVICE_READ request is sent to a device when an answer after
                    command execution is expected. SDG1000X-E sends this request
                    in two cases:
                        a.  It requests the ID of the AWG (*IDN? command).
                            In this case we MUST supply a valid ID to make
                            the scope think that it is working with a genuine
                            Siglent AWG.
                        b.  After setting all the parameters of the AWG and
                            before starting frequency sweep (C1:BSWV? command).
                            It looks like the scope is supposed to verify that
                            all the required AWG settings were set correctly.
                        In the real life it seems that in the second case the scope
                        totally ignores the response and will accept any garbage.
                        It makes our life easy and we send AWG ID as reply
                        to any DEVICE_READ request.
                    """
                    resp = self.generate_lxi_idn_response(AWG_ID_STRING)

                elif vxi11_procedure == DESTROY_LINK:
                    """
                    If DESTROY_LINK is received, the oscilloscope ends the session
                    opened by CREATE_LINK request and won't send any commands before
                    issuing a new CREATE_LINK request.
                    All we have to do is to exit the loop and continue listening to
                    RPCBIND requests.
                    """
                    resp = self.generate_lxi_destroy_link_response()

                else:
                    """
                    If the received command is none of the above, something
                    went wrong and we should exit the loop and continue
                    listening to RPCBIND requests.
                    """
                    break

                # Generate and send response
                xid = self.get_xid(rx_buf[0x04:])
                resp_data = self.generate_resp_data(xid, resp, False)
                connection.send(resp_data)
                
                if vxi11_procedure == DESTROY_LINK:
                    break

        # Close connection
        connection.close()

    def parse_lxi_request(self, rx_data):
        """Parses VXI-11 request. Returns VXI-11 command code and SCPI command if it exists.
        @param rx_data: bytes array containing the source packet.
        @return: a tuple with 3 values:
                1. status - is 0 if the request could be processed, error code otherwise.
                2. VXI-11 procedure id if it is known, None otherwise.
                3. string containing SCPI command if it exists in the request, in utf-8.
                4. the length of the sent command, in bytes (needed for some replies)."""
        # Validate source program id.
        #  If the request doesn't come from VXI-11 Core (395183), it is ignored.
        program_id = self.bytes_to_uint(rx_data[0x10:0x14])
        if program_id != VXI11_CORE_ID:
            return (NOT_VXI11_ERROR, None, None)

        # Procedure: CREATE_LINK (10), DESTROY_LINK (23), DEVICE_WRITE (11), DEVICE_READ (12)
        vxi11_procedure = self.bytes_to_uint(rx_data[0x18:0x1c])
        scpi_command = None
        status = OK
        cmd_length = 0

        # Process the remaining data according to the received VXI-11 request
        if vxi11_procedure == CREATE_LINK:
            cmd_length = self.bytes_to_uint(rx_data[0x38:0x3C])
            scpi_command = rx_data[0x3C:0x3C + cmd_length]
        elif vxi11_procedure == DEVICE_WRITE:
            cmd_length = self.bytes_to_uint(rx_data[0x3C:0x40])
            scpi_command = rx_data[0x40:0x40 + cmd_length]
        elif vxi11_procedure == DEVICE_READ:
            pass
        elif vxi11_procedure == DESTROY_LINK:
            pass
        else:
            status = UNKNOWN_COMMAND_ERROR
            print("Unknown VXI-11 command received. Code %s" % (vxi11_procedure))

        if scpi_command is not None:
            scpi_command = scpi_command.decode('utf-8').strip()
        return (status, vxi11_procedure, scpi_command, cmd_length)

    # =========================================================================
    #   Response data generators
    # =========================================================================

    def generate_lxi_create_link_response(self):
        """Generates reply to VXI-11 CREATE_LINK request."""
        # VXI-11 response
        #  Error Code: No Error (0)
        resp = b"\x00\x00\x00\x00"
        #  Link ID: 0
        resp += b"\x00\x00\x00\x00"
        #  Abort Port: 0
        resp += b"\x00\x00\x00\x00"
        #  Maximum Receive Size: 8388608=0x00800000
        # resp += self.uint_to_bytes(8388608)
        resp += b"\x00\x80\x00\x00"
        return resp
    
    def generate_lxi_destroy_link_response(self):
        """Generates reply to VXI-11 DESTROY_LINK request."""
        # VXI-11 response
        #  Error Code: No Error (0)
        resp = b"\x00\x00\x00\x00"
        return resp

    def generate_lxi_device_write_response(self, cmd_length):
        """Generates reply to VXI-11 DEVICE_WRITE request."""
        # VXI-11 response
        #  Error Code: No Error (0)
        resp = b"\x00\x00\x00\x00"
        #  Size: the size of the original command
        resp += self.uint_to_bytes(cmd_length)
        return resp        
        
    def generate_lxi_idn_response(self, id_string):
        """Generates reply to VXI-11 DEVICE_READ request."""
        # Error Code: No Error (0)
        resp = b"\x00\x00\x00\x00"
        # Reason: 0x00000004 (END)
        resp += b"\x00\x00\x00\x04"
        # Add the AWG id string
        id_length = len(id_string) + 1
        resp += self.uint_to_bytes(id_length)
        resp += id_string
        # The sequence ends with \n and two \0 fill bytes.
        resp += b"\x0A\x00\x00"
        return resp

    def close_lxi_sockets(self):
        """
        Closes VXI-11 socket.
        """
        try:
            self.lxi_socket.close()
        except:
            pass

    def close_sockets(self):
        self.close_lxi_sockets()
        if self.pm1:
            self.pm1.terminate()
        if self.pm2:
            self.pm2.terminate()
        
    def __del__(self):
        self.close_sockets()


if __name__ == '__main__':
    raise Exception("This module is not for running. Run bode.py instead.")
#     host = HOST
#     rpcbind_port = RPCBIND_PORT
#     vxi_port = VXI11_PORT
#
#     if len(sys.argv) == 2:
#         host = sys.argv[1]
#     elif len(sys.argv) == 3:
#         host = sys.argv[1]
#         rpcbind_port = int(sys.argv[2])

#     print "Listening on %s" % (host)
#     print "RPCBIND on port %s" % (rpcbind_port)
#     print "VXI-11 on port %s" % (vxi_port)
#
#     se = SigGenEmulator(host, rpcbind_port, vxi_port)
#
#     try:
#         se.run()
#
#     except KeyboardInterrupt:
#         print('Ctrl+C pressed. Exiting...')
#
#     finally:
#         se.close_sockets()
#         pass
