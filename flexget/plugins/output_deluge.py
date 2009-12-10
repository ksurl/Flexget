import logging
import time
import os
import base64
import urllib2
from flexget.plugin import *
from httplib import BadStatusLine

log = logging.getLogger('deluge')
        

class OutputDeluge:

    """
        Add the torrents directly to deluge, supporting custom save paths.
    """

    def validator(self):
        from flexget import validator
        root = validator.factory()
        root.accept('boolean')
        deluge = root.accept('dict')
        deluge.accept('text', key='host')
        deluge.accept('number', key='port')
        deluge.accept('text', key='user')
        deluge.accept('text', key='pass')
        deluge.accept('text', key='path')
        deluge.accept('text', key='movedone')
        deluge.accept('text', key='label')
        deluge.accept('boolean', key='queuetotop')
        deluge.accept('boolean', key='enabled')
        return root

    def get_config(self, feed):
        config = feed.config.get('deluge', {})
        if isinstance(config, bool):
            config = {'enabled': config}
        config.setdefault('host', 'localhost')
        config.setdefault('port', 58846)
        config.setdefault('user', '')
        config.setdefault('pass', '')
        config.setdefault('enabled', True)
        config.setdefault('path', '')
        config.setdefault('movedone', '')
        config.setdefault('label', '')
        config.setdefault('queuetotop', False)
        return config

    def __init__(self):
        self.deluge12 = False
        self.reactorRunning = 0

    def on_process_start(self, feed):
        """
            Register the usable set: keywords. Detect what version of deluge
            is loaded, start the reactor process if using the deluge 1.2 api.
        """
        set_plugin = get_plugin_by_name('set')
        set_plugin.instance.register_keys({'path': 'text', 'movedone': 'text', \
            'queuetotop': 'boolean', 'label': 'text'})
        if not self.deluge12:
            try:
                from deluge.ui.client import sclient
            except:
                try:
                    from multiprocessing import Process, Queue
                except:
                    raise PluginError('Python 2.6 required to use deluge 1.2 api', log)
                try:
                    from deluge.ui.client import client
                except:
                    raise PluginError('Deluge module required.', log)
                try:
                    from twisted.internet import reactor
                except:
                    raise PluginError('Twisted module required', log)
                log.info("Using deluge 1.2 api")
                self.deluge12 = True
            else:
                log.info("Using deluge 1.1 api")
                self.deluge12 = False
                
    def on_feed_download(self, feed):
        """
            call the feed_download method of download plugin
            this will generate the temp files we will load into deluge
            we don't need to call this if download plugin is loaded on this feed
        """
        config = self.get_config(feed)
        if not config['enabled']:
            return
        if not 'download' in feed.config:
            download = get_plugin_by_name('download')
            # download all content to temp folder, may fail some entries
            for entry in feed.accepted:
                try:
                    if feed.manager.options.test:
                        log.info('Would download: %s' % entry['title'])
                    else:
                        if not feed.manager.unit_test:
                            log.info('Downloading: %s' % entry['title'])
                        # check if entry must have a path (download: yes)
                        download.instance.download(feed, entry)
                except urllib2.HTTPError, e:
                    feed.fail(entry, 'HTTP error')
                    log.error('HTTPError %s' % e.code)
                except urllib2.URLError, e:
                    feed.fail(entry, 'URL Error')
                    log.error('URLError %s' % e.reason)
                except BadStatusLine:
                    feed.fail(entry, 'BadStatusLine')
                    log.error('Failed to reach server. Reason: %s' % e.reason)
                except IOError, e:
                    feed.fail(entry, 'IOError')
                    if hasattr(e, 'reason'):
                        log.error('Failed to reach server. Reason: %s' % e.reason)
                    elif hasattr(e, 'code'):
                        log.error('The server couldn\'t fulfill the request. Error code: %s' % e.code)

    def on_feed_output(self, feed):
        """Add torrents to deluge at exit."""
        config = self.get_config(feed)
        # don't add when learning
        if feed.manager.options.learn:
            return
        if not feed.accepted or not config['enabled']:
            return
            
        if self.deluge12:
            self.add_to_deluge12(feed, config)
        else:
            self.add_to_deluge11(feed, config)

    def add_to_deluge11(self, feed, config):
        """ Add torrents to deluge using deluge 1.1.x api. """
        try:
            from deluge.ui.client import sclient
        except:
            raise PluginError('Deluge module required', log)

        sclient.set_core_uri()
        opts = {}
        for entry in feed.accepted:
            try:
                before = sclient.get_session_state()
            except Exception, (errno, msg):
                raise PluginError('Could not communicate with deluge core. %s' % msg, log)
            if feed.manager.options.test:
                return
            path = entry.get('path', config['path'])
            if path:
                opts['download_location'] = os.path.expanduser(path % entry)
            
            # check that file is downloaded
            if not 'file' in entry:
                feed.fail(entry, 'file missing?')
                continue
                
            # see that temp file is present
            if not os.path.exists(entry['file']):
                tmp_path = os.path.join(feed.manager.config_base, 'temp')
                log.debug('entry: %s' % entry)
                log.debug('temp: %s' % ', '.join(os.listdir(tmp_path)))
                feed.fail(entry, "Downloaded temp file '%s' doesn't exist!?" % entry['file'])
                continue

            sclient.add_torrent_file([entry['file']], [opts])
            log.info("%s torrent added to deluge with options %s" % (entry['title'], opts))
            # clean up temp file if download plugin is not configured for this feed
            if not 'download' in feed.config:
                os.remove(entry['file'])
                del(entry['file'])
                
            movedone = entry.get('movedone', config['movedone'])
            label = entry.get('label', config['label']).lower()
            queuetotop = entry.get('queuetotop', config['queuetotop'])

            # Sometimes deluge takes a moment to add the torrent, wait a second.
            time.sleep(2)
            after = sclient.get_session_state()
            for item in after:
                # find torrentid of just added torrent
                if not item in before:
                    # entry['deluge_torrentid'] = item
                    if movedone:
                        log.debug("%s move on complete set to %s" % (entry['title'], movedone % entry))
                        sclient.set_torrent_move_on_completed(item, True)
                        sclient.set_torrent_move_on_completed_path(item, os.path.expanduser(movedone % entry))
                    if label:
                        if not "label" in sclient.get_enabled_plugins():
                            sclient.enable_plugin("label")
                        if not label in sclient.label_get_labels():
                            sclient.label_add(label)
                        log.debug("%s label set to '%s'" % (entry['title'], label))
                        sclient.label_set_torrent(item, label)
                    if queuetotop:
                        log.debug("%s moved to top of queue" % entry['title'])
                        sclient.queue_top([item])
                    break
            else:
                log.info("%s is already loaded in deluge. Cannot change label, movedone, or queuetotop" % entry['title'])                
                
    def add_to_deluge12(self, feed, config):
    
        """ This is the new add to deluge method, using iterate """
        
        from deluge.ui.client import client
        from twisted.internet import reactor, defer
        
        def start_reactor():
            if self.reactorRunning < 2:
                reactor.startRunning(True)
            self.reactorRunning = 1
            while self.reactorRunning == 1:
                reactor.iterate()
            if self.reactorRunning < 0:
                self.reactorRunning = 2
                raise PluginError('Could not connect to deluge daemon', log)
            self.reactorRunning = 2
                
        def pause_reactor(result):
            self.reactorRunning = result

        def on_connect_success(result, feed):
            if not result:
                # TODO: connect failed? do something
                pass
                
            def on_success(torrent_id, entry, opts):
                if not torrent_id:
                    log.info("%s is already loaded in deluge, cannot set movedone, label, or queuetotop." % entry['title'])
                    return
                log.info("%s successfully added to deluge." % entry['title'])
                if opts['movedone']:
                    if not os.path.isdir(opts['movedone']):
                        log.debug("movedone path %s doesn't exist, creating" % opts['movedone'])
                        os.makedirs(opts['movedone'])
                    client.core.set_torrent_move_completed(torrent_id, True)
                    client.core.set_torrent_move_completed_path(torrent_id, opts['movedone'])
                    log.debug("%s move on complete set to %s" % (entry['title'], opts['movedone']))
                if opts['label']:
                    client.core.enable_plugin('Label')
                    client.label.add(opts['label'])
                    client.label.set_torrent(torrent_id, opts['label'])
                    log.debug("%s label set to '%s'" % (entry['title'], opts['label']))
                if opts['queuetotop']:
                    client.core.queue_top([torrent_id])
                    log.debug("%s moved to top of queue" % entry['title'])

            def on_fail(result, feed, entry):
                log.info("%s was not added to deluge! %s" % (entry['title'], result))
                # TODO: Need to figure out how to fail entries properly from here.
                feed.fail(entry, "Could not be added to deluge")

            # add the torrents
            dlist = []
            for entry in feed.accepted:
                # see that temp file is present
                if not os.path.exists(entry['file']):
                    tmp_path = os.path.join(feed.manager.config_base, 'temp')
                    log.debug('entry: %s' % entry)
                    log.debug('temp: %s' % ', '.join(os.listdir(tmp_path)))
                    feed.fail(entry, "Downloaded temp file '%s' doesn't exist!" % entry['file'])
                    continue
                filedump = base64.encodestring(open(entry['file'], 'rb').read())
                path = os.path.expanduser(entry.get('path', config['path']) % entry)
                opts = {}
                if path:
                    opts['download_location'] = path
                addresult = client.core.add_torrent_file(entry['title'], filedump, opts)
                # clean up temp file if download plugin is not configured for this feed
                if not 'download' in feed.config:
                    os.remove(entry['file'])
                    del(entry['file'])

                # Make a new set of options, that get set after the torrent has been added
                opts = {}
                opts['movedone'] = os.path.expanduser(entry.get('movedone', config['movedone']) % entry)
                opts['label'] = entry.get('label', config['label']).lower()
                opts['queuetotop'] = entry.get('queuetotop', config['queuetotop'])
                addresult.addCallback(on_success, entry, opts).addErrback(on_fail, feed, entry)
                dlist.append(addresult)
                
            def on_complete(result):

                def on_disconnect(result):
                    log.debug('Done adding torrents to deluge.' % result)
                    reactor.callLater(0.1, pause_reactor, 0)
                    
                client.disconnect().addCallback(on_disconnect).addErrback(on_disconnect)

            defer.DeferredList(dlist).addCallback(on_complete)
            
        def on_connect_fail(result, feed):
            # clean up temp file if download plugin is not configured for this feed
            if not 'download' in feed.config:
                for entry in feed.accepted:
                    if entry['file'] and os.path.exists(entry['file']):
                        os.remove(entry['file'])
                        del(entry['file'])
            log.debug('Connect to deluge daemon failed, result: %s' % result)
            reactor.callLater(0.1, pause_reactor, -1)
            
        d = client.connect(
            host=config['host'],
            port=config['port'],
            username=config['user'],
            password=config['pass'])

        d.addCallback(on_connect_success, feed).addErrback(on_connect_fail, feed)
        start_reactor()

register_plugin(OutputDeluge, 'deluge', priorities=dict(output=1, process_start=1))
