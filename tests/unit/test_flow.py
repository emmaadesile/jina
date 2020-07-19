import os
import time
import unittest
from time import sleep

import pytest
import requests

from jina import JINA_GLOBAL
from jina.enums import FlowOptimizeLevel, SocketType
from jina.flow import Flow
from jina.main.checker import NetworkChecker
from jina.main.parser import set_pea_parser, set_ping_parser
from jina.main.parser import set_pod_parser
from jina.peapods.pea import BasePea
from jina.peapods.pod import BasePod
from jina.proto import jina_pb2
from tests import JinaTestCase, random_docs

cur_dir = os.path.dirname(os.path.abspath(__file__))


def random_queries(num_docs, chunks_per_doc=5, embed_dim=10):
    for j in range(num_docs):
        d = jina_pb2.Document()
        for k in range(chunks_per_doc):
            dd = d.matches.add()
            dd.match.id = k
        yield d


class FlowTestCase(JinaTestCase):

    def test_ping(self):
        a1 = set_pea_parser().parse_args([])
        a2 = set_ping_parser().parse_args(['0.0.0.0', str(a1.port_ctrl), '--print-response'])
        a3 = set_ping_parser().parse_args(['0.0.0.1', str(a1.port_ctrl), '--timeout', '1000'])

        with self.assertRaises(SystemExit) as cm:
            with BasePea(a1):
                NetworkChecker(a2)

        self.assertEqual(cm.exception.code, 0)

        # test with bad addresss
        with self.assertRaises(SystemExit) as cm:
            with BasePea(a1):
                NetworkChecker(a3)

        self.assertEqual(cm.exception.code, 1)

    def test_flow_with_jump(self):
        f = (Flow().add(name='r1', yaml_path='_forward')
             .add(name='r2', yaml_path='_forward')
             .add(name='r3', yaml_path='_forward', needs='r1')
             .add(name='r4', yaml_path='_forward', needs='r2')
             .add(name='r5', yaml_path='_forward', needs='r3')
             .add(name='r6', yaml_path='_forward', needs='r4')
             .add(name='r8', yaml_path='_forward', needs='r6')
             .add(name='r9', yaml_path='_forward', needs='r5')
             .add(name='r10', yaml_path='_merge', needs=['r9', 'r8']))

        with f:
            f.dry_run()

        self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
        self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['r1'].head_args.socket_in, SocketType.PULL_BIND)
        self.assertEqual(f._pod_nodes['r1'].tail_args.socket_out, SocketType.PUB_BIND)

        self.assertEqual(f._pod_nodes['r2'].head_args.socket_in, SocketType.SUB_CONNECT)
        self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['r3'].head_args.socket_in, SocketType.SUB_CONNECT)
        self.assertEqual(f._pod_nodes['r3'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['r4'].head_args.socket_in, SocketType.PULL_BIND)
        self.assertEqual(f._pod_nodes['r4'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['r5'].head_args.socket_in, SocketType.PULL_BIND)
        self.assertEqual(f._pod_nodes['r5'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['r6'].head_args.socket_in, SocketType.PULL_BIND)
        self.assertEqual(f._pod_nodes['r6'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['r8'].head_args.socket_in, SocketType.PULL_BIND)
        self.assertEqual(f._pod_nodes['r8'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['r9'].head_args.socket_in, SocketType.PULL_BIND)
        self.assertEqual(f._pod_nodes['r9'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['r10'].head_args.socket_in, SocketType.PULL_BIND)
        self.assertEqual(f._pod_nodes['r10'].tail_args.socket_out, SocketType.PUSH_BIND)

        for name, node in f._pod_nodes.items():
            self.assertEqual(node.peas_args['peas'][0], node.head_args)
            self.assertEqual(node.peas_args['peas'][0], node.tail_args)

        f.save_config('tmp.yml')
        Flow.load_config('tmp.yml')

        with Flow.load_config('tmp.yml') as fl:
            fl.dry_run()

        self.add_tmpfile('tmp.yml')

    def test_simple_flow(self):
        bytes_gen = (b'aaa' for _ in range(10))

        def bytes_fn():
            for _ in range(100):
                yield b'aaa'

        f = (Flow()
             .add(yaml_path='_forward'))

        with f:
            f.index(input_fn=bytes_gen)

        with f:
            f.index(input_fn=bytes_fn)

        with f:
            f.index(input_fn=bytes_fn)
            f.index(input_fn=bytes_fn)

        self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
        self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUSH_CONNECT)

        self.assertEqual(f._pod_nodes['pod0'].head_args.socket_in, SocketType.PULL_BIND)
        self.assertEqual(f._pod_nodes['pod0'].tail_args.socket_out, SocketType.PUSH_BIND)

        for name, node in f._pod_nodes.items():
            self.assertEqual(node.peas_args['peas'][0], node.head_args)
            self.assertEqual(node.peas_args['peas'][0], node.tail_args)

    def test_load_flow_from_yaml(self):
        with open(os.path.join(cur_dir, 'yaml/test-flow.yml')) as fp:
            a = Flow.load_config(fp)
            with open(os.path.join(cur_dir, 'yaml/swarm-out.yml'), 'w') as fp, a:
                a.to_swarm_yaml(fp)
            self.add_tmpfile(os.path.join(cur_dir, 'yaml/swarm-out.yml'))

    def test_flow_identical(self):
        with open(os.path.join(cur_dir, 'yaml/test-flow.yml')) as fp:
            a = Flow.load_config(fp)

        b = (Flow()
             .add(name='chunk_seg', replicas=3)
             .add(name='wqncode1', replicas=2)
             .add(name='encode2', replicas=2, needs='chunk_seg')
             .join(['wqncode1', 'encode2']))

        a.save_config('test2.yml')

        c = Flow.load_config('test2.yml')

        self.assertEqual(a, b)
        self.assertEqual(a, c)

        self.add_tmpfile('test2.yml')

        with a as f:
            self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
            self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['chunk_seg'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['chunk_seg'].head_args.socket_out, SocketType.ROUTER_BIND)
            for arg in f._pod_nodes['chunk_seg'].peas_args['peas']:
                self.assertEqual(arg.socket_in, SocketType.DEALER_CONNECT)
                self.assertEqual(arg.socket_out, SocketType.PUSH_CONNECT)
            self.assertEqual(f._pod_nodes['chunk_seg'].tail_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['chunk_seg'].tail_args.socket_out, SocketType.PUB_BIND)

            self.assertEqual(f._pod_nodes['wqncode1'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['wqncode1'].head_args.socket_out, SocketType.ROUTER_BIND)
            for arg in f._pod_nodes['wqncode1'].peas_args['peas']:
                self.assertEqual(arg.socket_in, SocketType.DEALER_CONNECT)
                self.assertEqual(arg.socket_out, SocketType.PUSH_CONNECT)
            self.assertEqual(f._pod_nodes['wqncode1'].tail_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['wqncode1'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['encode2'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['encode2'].head_args.socket_out, SocketType.ROUTER_BIND)
            for arg in f._pod_nodes['encode2'].peas_args['peas']:
                self.assertEqual(arg.socket_in, SocketType.DEALER_CONNECT)
                self.assertEqual(arg.socket_out, SocketType.PUSH_CONNECT)
            self.assertEqual(f._pod_nodes['encode2'].tail_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['encode2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

    def test_dryrun(self):
        f = (Flow()
             .add(name='dummyEncoder', yaml_path=os.path.join(cur_dir, 'mwu-encoder/mwu_encoder.yml')))

        with f:
            f.dry_run()

    def test_pod_status(self):
        args = set_pod_parser().parse_args(['--replicas', '3'])
        with BasePod(args) as p:
            self.assertEqual(len(p.status), p.num_peas)
            for v in p.status:
                self.assertIsNotNone(v)

    def test_flow_no_container(self):
        f = (Flow()
             .add(name='dummyEncoder', yaml_path=os.path.join(cur_dir, 'mwu-encoder/mwu_encoder.yml')))

        with f:
            f.index(input_fn=random_docs(10))

    def test_flow_yaml_dump(self):
        f = Flow(logserver_config=os.path.join(cur_dir, 'yaml/test-server-config.yml'),
                 optimize_level=FlowOptimizeLevel.IGNORE_GATEWAY,
                 no_gateway=True)
        f.save_config('test1.yml')

        fl = Flow.load_config('test1.yml')
        self.assertEqual(f.args.logserver_config, fl.args.logserver_config)
        self.assertEqual(f.args.optimize_level, fl.args.optimize_level)
        self.add_tmpfile('test1.yml')

    def test_flow_log_server(self):
        f = Flow.load_config(os.path.join(cur_dir, 'yaml/test_log_server.yml'))
        with f:
            self.assertTrue(hasattr(JINA_GLOBAL.logserver, 'ready'))

            # Ready endpoint
            a = requests.get(
                JINA_GLOBAL.logserver.address +
                '/status/ready',
                timeout=5)
            self.assertEqual(a.status_code, 200)

            # YAML endpoint
            a = requests.get(
                JINA_GLOBAL.logserver.address +
                '/data/yaml',
                timeout=5)
            self.assertTrue(a.text.startswith('!Flow'))
            self.assertEqual(a.status_code, 200)

            # Pod endpoint
            a = requests.get(
                JINA_GLOBAL.logserver.address +
                '/data/api/pod',
                timeout=5)
            self.assertTrue('pod' in a.json())
            self.assertEqual(a.status_code, 200)

            # Shutdown endpoint
            a = requests.get(
                JINA_GLOBAL.logserver.address +
                '/action/shutdown',
                timeout=5)
            self.assertEqual(a.status_code, 200)

            # Check ready endpoint after shutdown, check if server stopped
            with self.assertRaises(requests.exceptions.ConnectionError):
                a = requests.get(
                    JINA_GLOBAL.logserver.address +
                    '/status/ready',
                    timeout=5)

    def test_shards(self):
        f = Flow().add(name='doc_pb', yaml_path=os.path.join(cur_dir, 'yaml/test-docpb.yml'), replicas=3,
                       separated_workspace=True)
        with f:
            f.index(input_fn=random_docs(1000), randomize_id=False)
        with f:
            pass
        self.add_tmpfile('test-docshard-tmp')
        time.sleep(2)

    def test_shards_insufficient_data(self):
        """THIS IS SUPER IMPORTANT FOR TESTING SHARDS

        IF THIS FAILED, DONT IGNORE IT, DEBUG IT
        """
        index_docs = 3
        replicas = 4

        def validate(req):
            self.assertEqual(len(req.docs), 1)
            self.assertEqual(len(req.docs[0].matches), index_docs)

            for d in req.docs[0].matches:
                self.assertTrue(hasattr(d.match, 'weight'))
                self.assertIsNotNone(d.match.weight)
                self.assertEqual(d.match.meta_info, b'hello world')

        f = Flow().add(name='doc_pb', yaml_path=os.path.join(cur_dir, 'yaml/test-docpb.yml'), replicas=replicas,
                       separated_workspace=True)
        with f:
            f.index(input_fn=random_docs(index_docs), randomize_id=False)

        time.sleep(2)
        with f:
            pass
        time.sleep(2)
        f = Flow().add(name='doc_pb', yaml_path=os.path.join(cur_dir, 'yaml/test-docpb.yml'), replicas=replicas,
                       separated_workspace=True, polling='all', reducing_yaml_path='_merge_all')
        with f:
            f.search(input_fn=random_queries(1, index_docs), randomize_id=False, output_fn=validate,
                     callback_on_body=True)
        time.sleep(2)
        self.add_tmpfile('test-docshard-tmp')

    def test_py_client(self):
        f = (Flow().add(name='r1', yaml_path='_forward')
             .add(name='r2', yaml_path='_forward')
             .add(name='r3', yaml_path='_forward', needs='r1')
             .add(name='r4', yaml_path='_forward', needs='r2')
             .add(name='r5', yaml_path='_forward', needs='r3')
             .add(name='r6', yaml_path='_forward', needs='r4')
             .add(name='r8', yaml_path='_forward', needs='r6')
             .add(name='r9', yaml_path='_forward', needs='r5')
             .add(name='r10', yaml_path='_merge', needs=['r9', 'r8']))

        with f:
            f.dry_run()
            from jina.clients import py_client
            py_client(port_expose=f.port_expose, host=f.host).dry_run(as_request='index')

        with f:
            self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
            self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r1'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r1'].tail_args.socket_out, SocketType.PUB_BIND)

            self.assertEqual(f._pod_nodes['r2'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r3'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r3'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r4'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r4'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r5'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r5'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r6'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r6'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r8'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r8'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r9'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r9'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r10'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r10'].tail_args.socket_out, SocketType.PUSH_BIND)

            for name, node in f._pod_nodes.items():
                self.assertEqual(node.peas_args['peas'][0], node.head_args)
                self.assertEqual(node.peas_args['peas'][0], node.tail_args)

    def test_dry_run_with_two_pathways_diverging_at_gateway(self):
        f = (Flow().add(name='r2', yaml_path='_forward')
             .add(name='r3', yaml_path='_forward', needs='gateway')
             .join(['r2', 'r3']))

        with f:
            self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
            self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUB_BIND)

            self.assertEqual(f._pod_nodes['r2'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r3'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r3'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            for name, node in f._pod_nodes.items():
                self.assertEqual(node.peas_args['peas'][0], node.head_args)
                self.assertEqual(node.peas_args['peas'][0], node.tail_args)

            f.dry_run()

    def test_dry_run_with_two_pathways_diverging_at_non_gateway(self):
        f = (Flow().add(name='r1', yaml_path='_forward')
             .add(name='r2', yaml_path='_forward')
             .add(name='r3', yaml_path='_forward', needs='r1')
             .join(['r2', 'r3']))

        with f:
            self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
            self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r1'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r2'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r3'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r3'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            for name, node in f._pod_nodes.items():
                self.assertEqual(node.peas_args['peas'][0], node.head_args)
                self.assertEqual(node.peas_args['peas'][0], node.tail_args)
            f.dry_run()

    @pytest.mark.skip('this leads to zmq address conflicts on github')
    def test_refactor_num_part(self):
        sleep(3)
        f = (Flow().add(name='r1', yaml_path='_logforward', needs='gateway')
             .add(name='r2', yaml_path='_logforward', needs='gateway')
             .join(['r1', 'r2']))

        with f:
            self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
            self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r1'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r2'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            for name, node in f._pod_nodes.items():
                self.assertEqual(node.peas_args['peas'][0], node.head_args)
                self.assertEqual(node.peas_args['peas'][0], node.tail_args)

            f.index_lines(lines=['abbcs', 'efgh'])

    def test_refactor_num_part_proxy(self):
        f = (Flow().add(name='r1', yaml_path='_logforward')
             .add(name='r2', yaml_path='_logforward', needs='r1')
             .add(name='r3', yaml_path='_logforward', needs='r1')
             .join(['r2', 'r3']))

        with f:
            self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
            self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r1'].head_args.socket_in, SocketType.PULL_BIND)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r2'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r3'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r3'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            for name, node in f._pod_nodes.items():
                self.assertEqual(node.peas_args['peas'][0], node.head_args)
                self.assertEqual(node.peas_args['peas'][0], node.tail_args)

            f.index_lines(lines=['abbcs', 'efgh'])

    def test_refactor_num_part_proxy_2(self):
        f = (Flow().add(name='r1', yaml_path='_logforward')
             .add(name='r2', yaml_path='_logforward', needs='r1', replicas=2)
             .add(name='r3', yaml_path='_logforward', needs='r1', replicas=3, polling='ALL')
             .join(['r2', 'r3']))

        with f:
            f.index_lines(lines=['abbcs', 'efgh'])

    def test_refactor_num_part_2(self):
        f = (Flow()
             .add(name='r1', yaml_path='_logforward', needs='gateway', replicas=3, polling='ALL'))

        with f:
            f.index_lines(lines=['abbcs', 'efgh'])

        f = (Flow()
             .add(name='r1', yaml_path='_logforward', needs='gateway', replicas=3))

        with f:
            f.index_lines(lines=['abbcs', 'efgh'])

    def test_index_text_files(self):

        def validate(req):
            for d in req.docs:
                self.assertNotEqual(d.text, '')

        f = (Flow(read_only=True).add(yaml_path=os.path.join(cur_dir, 'yaml/datauriindex.yml'), timeout_ready=-1))

        with f:
            f.index_files('*.py', output_fn=validate, callback_on_body=True)

        self.add_tmpfile('doc.gzip')

    def test_flow_with_unary_segment_driver(self):

        f = (Flow()
             .add(name='r2', yaml_path='!OneHotTextEncoder')
             .add(name='r3', yaml_path='!OneHotTextEncoder', needs='gateway')
             .join(needs=['r2', 'r3']))

        def validate(req):
            for d in req.docs:
                self.assertIsNotNone(d.embedding)

        with f:
            self.assertEqual(f._pod_nodes['gateway'].head_args.socket_in, SocketType.PULL_CONNECT)
            self.assertEqual(f._pod_nodes['gateway'].tail_args.socket_out, SocketType.PUB_BIND)

            self.assertEqual(f._pod_nodes['r2'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r2'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            self.assertEqual(f._pod_nodes['r3'].head_args.socket_in, SocketType.SUB_CONNECT)
            self.assertEqual(f._pod_nodes['r3'].tail_args.socket_out, SocketType.PUSH_CONNECT)

            for name, node in f._pod_nodes.items():
                self.assertEqual(node.peas_args['peas'][0], node.head_args)
                self.assertEqual(node.peas_args['peas'][0], node.tail_args)

            f.index_lines(lines=['text_1', 'text_2'], output_fn=validate, callback_on_body=True)


if __name__ == '__main__':
    unittest.main()
