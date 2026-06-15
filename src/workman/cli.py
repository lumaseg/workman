import argparse
import sys
from workman import session

def main():
    parser = argparse.ArgumentParser(
        prog='workman',
        description='Save and restore your desktop sessions'
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    save_parser = subparsers.add_parser('save', help='Save current session')
    save_parser.add_argument('name', help='Session name')

    restore_parser = subparsers.add_parser('restore', help='Restore a session')
    restore_parser.add_argument('name', help='Session name')
    restore_parser.add_argument(
        '--close-others',
        action='store_true',
        help='Close apps that are open but not part of this session'
    )

    subparsers.add_parser('list', help='List all saved sessions')

    delete_parser = subparsers.add_parser('delete', help='Delete a session')
    delete_parser.add_argument('name', help='Session name')

    args = parser.parse_args()

    try:
        if args.command == 'save':
            session.save_session(args.name)
        elif args.command == 'restore':
            session.restore_session(args.name, close_others=args.close_others)
        elif args.command == 'list':
            session.list_sessions()
        elif args.command == 'delete':
            session.delete_session(args.name)
        else:
            parser.print_help()
            sys.exit(1)
    except session.WorkmanError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
