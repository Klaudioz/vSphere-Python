"""
random-vmotion is a Python script which takes a list of VMs and a list of Hosts and will vMotion VMs randomly between those hosts per a provided interval. It will continue to do so until you stop it.

This script has the following capabilities:
    * vMotion VMs to a random host
    * Continue until stopped
    * Print logging to a log file or stdout

--- Usage ---
Run 'random-vmotion.py -h' for an overview

--- Documentation ---
https://github.com/pdellaert/vSphere-Python/blob/master/docs/random-vmotion.md

--- Author ---
Philippe Dellaert <philippe@dellaert.org>

--- License ---
https://raw.github.com/pdellaert/vSphere-Python/master/LICENSE.md

"""

import argparse
import atexit
import csv
import getpass
import json
import multiprocessing
import logging
import os.path
import re
import requests
import subprocess
import sys

from time import sleep
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim, vmodl
from multiprocessing.dummy import Pool as ThreadPool

def get_args():
    """
    Supports the command-line arguments listed below.
    """

    parser = argparse.ArgumentParser(description="Randomly vMotion each VM from a list one by one to a random host from a list, until stopped.")
    parser.add_argument('-d', '--debug', required=False, help='Enable debug output', dest='debug', action='store_true')
    parser.add_argument('-H', '--host', nargs=1, required=True, help='The vCenter or ESXi host to connect to', dest='host', type=str)
    parser.add_argument('-i', '--interval', nargs=1, required=False, help='The amount of time to wait after a vMotion is finished to schedule a new one (default 30 seconds)', dest='wait', type=int, default=[30])
    parser.add_argument('-l', '--log-file', nargs=1, required=False, help='File to log to (default = stdout)', dest='logfile', type=str)
    parser.add_argument('-o', '--port', nargs=1, required=False, help='Server port to connect to (default = 443)', dest='port', type=int, default=[443])
    parser.add_argument('-p', '--password', nargs=1, required=False, help='The password with which to connect to the host. If not specified, the user is prompted at runtime for a password', dest='password', type=str)
    parser.add_argument('-S', '--disable-SSL-certificate-verification', required=False, help='Disable SSL certificate verification on connect', dest='nosslcheck', action='store_true')
    parser.add_argument('-t', '--targets', nargs=1, required=True, help='File with the list of target hosts to vMotion to', dest='targetfile', type=str)
    parser.add_argument('-T', '--threads', nargs=1, required=False, help='Amount of simultanious vMotions to execute at once. (default = 1)', dest='threads', type=int, default=[1])
    parser.add_argument('-u', '--user', nargs=1, required=True, help='The username with which to connect to the host', dest='username', type=str)
    parser.add_argument('-v', '--verbose', required=False, help='Enable verbose output', dest='verbose', action='store_true')
    parser.add_argument('-V', '--vms', nargs=1, required=True, help='File with the list of VMs to vMotion', dest='vmfile', type=str)
    args = parser.parse_args()
    return args

def find_vm(si,logger,name,threaded=False):
    """
    Find a virtual machine by it's name and return it
    """

    content = si.content
    obj_view = content.viewManager.CreateContainerView(content.rootFolder,[vim.VirtualMachine],True)
    vm_list = obj_view.view

    for vm in vm_list:
        if threaded:
            logger.debug('THREAD %s - Checking virtual machine %s' % (name,vm.name))
        else:
            logger.debug('Checking virtual machine %s' % vm.name)
        if vm.name == name:
            if threaded:
                logger.debug('THREAD %s - Found virtual machine %s' % (name,vm.name))
            else:
                logger.debug('Found virtual machine %s' % vm.name)
            return vm
    return None

def find_host(si,logger,name,threaded=False):
    """
    Find a host by it's name and return it
    """

    content = si.content
    obj_view = content.viewManager.CreateContainerView(content.rootFolder,[vim.HostSystem],True)
    host_list = obj_view.view

    for host in host_list:
        if threaded:
            logger.debug('THREAD %s - Checking host %s' % (name,host.name))
        else:
            logger.debug('Checking host %s' % host.name)
        if host.name == host:
            if threaded:
                logger.debug('THREAD %s - Found host %s' % (name,host.name))
            else:
                logger.debug('Found host %s' % host.name)
            return host
    return None

def vm_vmotion_handler_wrapper(args):
    """
    Wrapping arround vm_clone_handler
    """

    return vm_vmotion_handler(*args)

def vm_vmotion_handler(si,logger,vm_name,host):
    """
    Will handle the thread handling to vMotion a virtual machine
    """

    run_loop = True
    vm = None

    logger.debug('THREAD %s - started' % vm_name)

def main():
    """
    Clone a VM or template into multiple VMs with logical names with numbers and allow for post-processing
    """

    # Handling arguments
    args = get_args()
    debug       = args.debug
    host        = args.host[0]
    interval    = args.interval[0]
    log_file= None
    if args.logfile:
        log_file = args.logfile[0]
    port        = args.port[0]
    password = None
    if args.password:
        password = args.password[0]
    nosslcheck  = args.nosslcheck
    targetfile  = args.targetfile[0]
    threads     = args.threads[0]
    username    = args.username[0]
    verbose     = args.verbose
    vmfile      = args.vmfile[0]

    # Logging settings
    if debug:
        log_level = logging.DEBUG
    elif verbose:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    if log_file:
        logging.basicConfig(filename=log_file,format='%(asctime)s %(levelname)s %(message)s',level=log_level)
    else:
        logging.basicConfig(filename=log_file,format='%(asctime)s %(levelname)s %(message)s',level=log_level)
    logger = logging.getLogger(__name__)

    # Disabling SSL verification if set
    if nosslcheck:
        logger.debug('Disabling SSL certificate verification.')
        requests.packages.urllib3.disable_warnings()

    # Getting user password
    if password is None:
        logger.debug('No command line password received, requesting password from user')
        password = getpass.getpass(prompt='Enter password for vCenter %s for user %s: ' % (host,username))

    try:
        si = None
        try:
            logger.info('Connecting to server %s:%s with username %s' % (host,port,username))
            si = SmartConnect(host=host,user=username,pwd=password,port=int(port))
        except IOError, e:
            pass

        if not si:
            logger.error('Could not connect to host %s with user %s and specified password' % (host,username))
            return 1

        logger.debug('Registering disconnect at exit')
        atexit.register(Disconnect, si)

        # Handling vms file
        logger.debug('Parsing VMs %s' % vmfile)

        if not os.path.isfile(vmfile):
            logger.critical('VM file %s does not exist, exiting' % vmfile)
            return 1

        # Getting VMs
        vms = []
        with open(vmfile,'rb') as tasklist:
            taskreader = csv.reader(tasklist,delimiter=';',quotechar="'")
            for row in taskreader:
                logger.debug('Found CSV row: %s' % ','.join(row))
                # VM Name
                if row[0] is None or row[0] is '':
                    logger.warning('No VM name specified, skipping this vm')
                    continue
                else:
                    cur_vm_name = row[0]

                # Finding VM
                cur_vm = find_vm(si,logger,cur_vm_name)

                # Adding VM to list
                if cur_vm is not None:
                    vms.append(cur_vm)
                else:
                    logger.warning('VM %s does not exist, skipping this vm' % cur_vm_name)

        # Getting hosts
        hosts = []
        with open(targetfile,'rb') as tasklist:
            taskreader = csv.reader(tasklist,delimiter=';',quotechar="'")
            for row in taskreader:
                logger.debug('Found CSV row: %s' % ','.join(row))
                # Host Name
                if row[0] is None or row[0] is '':
                    logger.warning('No host name specified, skipping this host')
                    continue
                else:
                    cur_host_name = row[0]

                # Finding Host
                cur_host = find_host(si,logger,cur_host_name)

                # Adding Host to list
                if cur_host is not None:
                    hosts.append(cur_host)
                else:
                    logger.warning('Host %s does not exist, skipping this host' % cur_host_name)


        # COUNT IF LIST IS LONGER THAN NUM THREADS
        # IF NOT, WARN AND CREATE SMALLER POOL (EQUAL TO THREADS)
        # CREATE POOL
        # FILL POOL
        # WHATCH POOL, IF TASK DONE, ADD TASK
        # CAPTURE CTRL-C TO STOP

        if len(vms) < threads:
            logger.warning('Amount of threads %s can not be higher than amount of vms: Setting amount of threads to %s' % (threads,len(vms)))
            threads = len(vms)
        
        # Pool handling
        logger.debug('Setting up pools and threads')
        pool = ThreadPool(threads)
        logger.debug('Pools created with %s threads' % threads)

    except vmodl.MethodFault, e:
        logger.critical('Caught vmodl fault: %s' % e.msg)
        return 1
    except Exception, e:
        logger.critical('Caught exception: %s' % str(e))
        return 1

    logger.info('Finished all tasks')
    return 0

# Start program
if __name__ == "__main__":
    main()
