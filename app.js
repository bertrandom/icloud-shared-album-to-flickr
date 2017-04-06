var rp = require('request-promise-native');
var config = require('config');
var flickr = require('flickr-upload')(config);
var request = require('request');
var Queue = require('promise-queue');
var level = require('level');

function getBaseUrl(token) {

    var BASE_62_CHAR_SET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

    var base62ToInt = function(e) {
        var t = 0;
        for (var n = 0; n < e.length; n++) t = t * 62 + BASE_62_CHAR_SET.indexOf(e[n]);
        return t
    };

    var e = token,
        t = e[0],
        n = t === "A" ? base62ToInt(e[1]) : base62ToInt(e.substring(1, 3)),
        r = e,
        i = e.indexOf(";"),
        s = null;

    if (i >= 0) {
        s = e.slice(i + 1);
        r = r.replace(";" + s, "");      
    }

    var serverPartition = n;

    var baseUrl = 'https://p';

    baseUrl += (serverPartition < 10) ? "0" + serverPartition : serverPartition;
    baseUrl += '-sharedstreams.icloud.com';
    baseUrl += '/';
    baseUrl += token;
    baseUrl += '/sharedstreams/'

    return baseUrl;

}

function getPhotoMetadata(baseUrl) {

    var url = baseUrl + 'webstream';

    var headers = {
        'Origin': 'https://www.icloud.com',
        'Accept-Language': 'en-US,en;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/56.0.2924.87 Safari/537.36',
        'Content-Type': 'text/plain',
        'Accept': '*/*',
        'Referer': 'https://www.icloud.com/sharedalbum/',
        'Connection': 'keep-alive'
    };

    var dataString = '{"streamCtag":null}';

    var options = {
        url: url,
        method: 'POST',
        headers: headers,
        body: dataString
    };

    return rp(options).then(function (body) {

        var data = JSON.parse(body);

        var photos = {};

        var photoGuids = [];

        data.photos.forEach(function(photo) {
            photos[photo.photoGuid] = photo;
            photoGuids.push(photo.photoGuid);
        });

        return {
            photos: photos,
            photoGuids: photoGuids
        };

    });

}

function getUrls(baseUrl, photoGuids) {

    var url = baseUrl + 'webasseturls';

    var headers = {
        'Origin': 'https://www.icloud.com',
        'Accept-Language': 'en-US,en;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/56.0.2924.87 Safari/537.36',
        'Content-Type': 'text/plain',
        'Accept': '*/*',
        'Referer': 'https://www.icloud.com/sharedalbum/',
        'Connection': 'keep-alive'
    };

    var dataString = JSON.stringify({
        photoGuids: photoGuids
    });

    var options = {
        url: url,
        method: 'POST',
        headers: headers,
        body: dataString
    };

    return rp(options).then(function (body) {

        var data = JSON.parse(body);

        var items = {};

        for (var itemId in data.items) {
            var item = data.items[itemId];

            items[itemId] = 'https://' + item.url_location + item.url_path;

        }

        return items;

    });

}

function decorateUrls(metadata, urls) {

    for (var photoId in metadata.photos) {
        var photo = metadata.photos[photoId];

        var biggestFileSize = 0;
        var bestDerivative = null;

        for (var derivativeId in photo.derivatives) {
            var derivative = photo.derivatives[derivativeId];

            if (parseInt(derivative.fileSize, 10) > biggestFileSize) {
                biggestFileSize = parseInt(derivative.fileSize, 10);
                bestDerivative = derivative;
            }
        }

        if (bestDerivative) {
            var url = urls[bestDerivative.checksum];
            metadata.photos[photoId].url = url;
            metadata.photos[photoId].bestDerivative = bestDerivative;
        }

    }

}

function uploadFlickr(photo) {

    return new Promise(function(resolve, reject) {

        flickr.upload(request(photo.url), {title: photo.caption ? photo.caption : '', is_public: 0, strip_filename: true}, function(err, photoId) {

            if (err) {
                return reject(err);
            }

            resolve({
                photo: photo,
                photoId: photoId
            });

        });

    });

}

var baseUrl = getBaseUrl(config.icloud_album_token);

getPhotoMetadata(baseUrl).then(function(metadata) {

    getUrls(baseUrl, metadata.photoGuids).then(function (urls) {

        decorateUrls(metadata, urls);

        var queue = new Queue(1, Infinity);

        var leveldb = level('./photosdb');
        var db = require('level-promisify')(leveldb);

        var checks = [];

        for (var photoGuid in metadata.photos) {

            (function() {

                var photoGuid = this;
                db.get(photoGuid, function (err, value) {

                    if (err) {

                        var photo = metadata.photos[photoGuid];

                        var generator = function() {
                            return uploadFlickr(photo);
                        };

                        queue.add(generator).then(function(data) {
                            db.put(data.photo.photoGuid, data.photoId, function(err) {
                                console.log('Uploaded ' + data.photo.photoGuid + ' as ' + data.photoId);
                            });
                        });

                    } else {
                        console.log('Skipping ' + photoGuid);
                    }

                });

            }.bind(photoGuid))();

        }

    });

});
