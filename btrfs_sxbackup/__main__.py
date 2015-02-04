import logging
import logging.handlers
import sys
from subprocess import CalledProcessError
import traceback
from argparse import ArgumentParser

import btrfs_sxbackup.commands
from btrfs_sxbackup.commands import Error
from btrfs_sxbackup.configs import Configuration
from btrfs_sxbackup import mail
from btrfs_sxbackup import __version__


_APP_NAME = 'btrfs-sxbackup'

_CMD_INIT = 'init'
_CMD_UPDATE = 'update'
_CMD_RUN = 'run'
_CMD_INFO = 'info'
_CMD_SEND = 'send'
_CMD_DESTROY = 'destroy'

# Parse arguments
parser = ArgumentParser(prog=_APP_NAME)
parser.add_argument('-q', '--quiet', dest='quiet', action='store_true', default=False,
                    help='do not log to stdout')
parser.add_argument('--trace', dest='trace', action='store_true', default=False, help='enables trace output')
parser.add_argument('--version', action='version', version='%s v%s' % (_APP_NAME, __version__))

subparsers = parser.add_subparsers()
subparsers.required = True
subparsers.dest = 'command'

# Reusable options
compress_args = ['-c', '--compress']
compress_kwargs = {'action': 'store_true',
                   'help': 'enables compression during transmission. Requires lzop to be installed on both source'
                           ' and destination'}

source_retention_args = ['-sr', '--source-retention']
source_retention_kwargs = {'type': str,
                           'default': None,
                           'help': 'expression defining which source snapshots to retain/cleanup.'
                                   ' Can be a static number (of backups) or more complex expression like'
                                   ' "1d:4/d, 1w:daily, 2m:none" literally translating to: "1 day from now keep'
                                   ' 4 backups a day, 1 week from now keep daily backups,'
                                   ' 2 months from now keep none".'}

destination_retention_args = ['-dr', '--destination-retention']
destination_retention_kwargs = {'type': str,
                                'default': None,
                                'help': 'expression defining which destination snapshots to retain/cleanup.'
                                        ' Can be a static number (of backups) or more complex'
                                        ' expression (see --source-retention argument).'}

# Initialize command cmdline params
p_init = subparsers.add_parser(_CMD_INIT, help='initialize backup job')
p_init.add_argument('source_subvolume', type=str, metavar='source-subvolume',
                    help='source subvolume to backup. Local path or SSH url.')
p_init.add_argument('destination_subvolume', type=str, metavar='destination-subvolume',
                    help='destination subvolume receiving backup snapshots. Local path or SSH url.')
p_init.add_argument(*source_retention_args, **source_retention_kwargs)
p_init.add_argument(*destination_retention_args, **destination_retention_kwargs)
p_init.add_argument(*compress_args, **compress_kwargs)

p_destroy = subparsers.add_parser(_CMD_DESTROY, help='destroy backup job')
p_destroy.add_argument('subvolume', type=str, help='Backup job subvolume. Local path or SSH url.')
p_destroy.add_argument('--purge', action='store_true', help='removes all backup snapshots from source and destination')

# Update command cmdline params
p_update = subparsers.add_parser(_CMD_UPDATE, help='update backup job')
p_update.add_argument('subvolume', type=str, help='Source or destination subvolume. Local path or SSH url.')
p_update.add_argument(*source_retention_args, **source_retention_kwargs)
p_update.add_argument(*destination_retention_args, **destination_retention_kwargs)
p_update.add_argument(*compress_args, **compress_kwargs)

# Run command cmdline params
p_run = subparsers.add_parser(_CMD_RUN, help='run backup job')
p_run.add_argument('subvolume', type=str,
                   help='source or destination subvolume. Local path or SSH url.')
p_run.add_argument('-m', '--mail', type=str, nargs='?', const='',
                   help='enables email notifications. If an email address is given, it overrides the'
                        ' default email-recipient setting in /etc/btrfs-sxbackup.conf')
p_run.add_argument('-li', '--log-ident', dest='log_ident', type=str, default=None,
                   help='log ident used for syslog logging, defaults to script name')

# Info command cmdline params
p_info = subparsers.add_parser(_CMD_INFO, help='backup job info')
p_info.add_argument('subvolume', type=str,
                    help='subvolume')

# Send command cmdline params
p_send = subparsers.add_parser(_CMD_SEND, help='send snapshot')
p_send.add_argument('source-subvolume', type=str,
                    help='name of the snapshot to transfer')
p_send.add_argument('destination-subvolume', type=str,
                    help='destination subvolume receiving the snapshot. Local psth or SSH url.')
p_send.add_argument(*compress_args, **compress_kwargs)


# Initialize logging
args = parser.parse_args()

# Read global configuration
Configuration.instance().read()

logger = logging.getLogger()

if not args.quiet:
    log_std_handler = logging.StreamHandler(sys.stdout)
    log_std_handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    logger.addHandler(log_std_handler)

log_memory_handler = None
email_recipient = None

# Syslog handler
if args.command == _CMD_RUN:
    log_syslog_handler = logging.handlers.SysLogHandler('/dev/log')
    log_syslog_handler.setFormatter(logging.Formatter(_APP_NAME + '[%(process)d] %(levelname)s %(message)s'))
    logger.addHandler(log_syslog_handler)

    # Log ident support
    if args.log_ident:
        log_ident = args.log_ident if args.log_ident else Configuration.instance().log_ident
        if log_ident:
            log_syslog_handler.ident = log_ident + ' '

    # Mail notification support
    if args.mail is not None:
        email_recipient = args.mail if len(args.mail) > 0 else Configuration.instance().email_recipient

        # Memory handler will buffer output for sending via mail later if needed
        log_memory_handler = logging.handlers.MemoryHandler(capacity=-1)
        log_memory_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(log_memory_handler)

logger.setLevel(logging.INFO)
logger.info('%s v%s' % (_APP_NAME, __version__))

try:
    if args.command == _CMD_RUN:
        btrfs_sxbackup.commands.run(args.subvolume)

    elif args.command == _CMD_INIT:
        btrfs_sxbackup.commands.init(
            source_url=args.source_subvolume,
            source_retention=args.source_retention,
            dest_url=args.destination_subvolume,
            dest_retention=args.destination_retention,
            compress=args.compress)

    elif args.command == _CMD_UPDATE:
        btrfs_sxbackup.commands.update(args.subvolume,
                                       source_retention=args.source_retention,
                                       dest_retention=args.destination_retention,
                                       compress=args.compress)

    elif args.command == _CMD_DESTROY:
        btrfs_sxbackup.commands.destroy(args.subvolume, args.purge)

    elif args.command == _CMD_INFO:
        btrfs_sxbackup.commands.info(args.subvolume)

    elif args.command == _CMD_SEND:
        btrfs_sxbackup.commands.send(args.source_subvolume, args.destination_subvolume)

except SystemExit as e:
    if e.code != 0:
        raise

except BaseException as e:
    # Log exception message
    e_msg = str(e)
    if len(e_msg) > 0:
        logger.error('%s' % e)

    if isinstance(e, CalledProcessError):
        if e.output:
            output = e.output.decode().strip()
            if len(output) > 0:
                logger.error('%s' % output)

    if args.trace:
        # Log stack trace
        logger.error(traceback.format_exc())

    # Email notification
    if email_recipient:
        # Format message and send
        msg = '\n'.join(map(lambda log_record: log_memory_handler.formatter.format(log_record),
                            log_memory_handler.buffer))
        mail.send(email_recipient, '%s FAILED' % _APP_NAME, msg)
    exit(1)

exit(0)

