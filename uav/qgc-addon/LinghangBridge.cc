#include "LinghangBridge.h"

#include <cmath>

#include <QtCore/QCryptographicHash>
#include <QtCore/QDateTime>
#include <QtCore/QFile>
#include <QtCore/QJsonDocument>
#include <QtCore/QUuid>
#include <QtHttpServer/QHttpServerRequest>
#include <QtHttpServer/QHttpServerResponse>
#include <QtNetwork/QHostAddress>

using Status = QHttpServerResponder::StatusCode;

LinghangBridge::LinghangBridge(QObject* parent)
    : QObject(parent)
    , _instanceId(QUuid::createUuid().toByteArray())
{
    QFile tokenFile(qEnvironmentVariable("QGC_LINGHANG_TOKEN_FILE"));
    if (!tokenFile.open(QIODevice::ReadOnly)) {
        _statusText = tr("助手桥未启用：未指定令牌文件");
        return;
    }
    _token = tokenFile.readAll().trimmed();
    if (_token.size() < 32) {
        _statusText = tr("助手桥令牌无效");
        return;
    }
    _http.route("/snapshot", QHttpServerRequest::Method::Get, this, [this](const QHttpServerRequest& request) {
        if (!authorized(request)) {
            return QHttpServerResponse(Status::Unauthorized);
        }
        if (!_controller) {
            return QHttpServerResponse(Status::ServiceUnavailable);
        }
        refresh();
        return QHttpServerResponse(snapshot());
    });
    _http.route("/preview", QHttpServerRequest::Method::Post, this, [this](const QHttpServerRequest& request) {
        if (!authorized(request)) {
            return QHttpServerResponse(Status::Unauthorized);
        }
        if (!_controller || _controller->syncInProgress()) {
            return QHttpServerResponse(Status::Conflict);
        }
        if (request.body().size() > 1024 * 1024) {
            return QHttpServerResponse(Status::PayloadTooLarge);
        }
        refresh();
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(request.body(), &parseError);
        const QJsonObject body = document.object();
        if (parseError.error != QJsonParseError::NoError || !body.value("plan").isObject()) {
            return QHttpServerResponse(Status::BadRequest);
        }
        if (body.value("expectedRevision").toInteger(-1) != _revision) {
            return QHttpServerResponse(Status::Conflict);
        }
        QString error;
        if (!_controller->loadLinghangDraft(body.value("plan").toObject(), error)) {
            return QHttpServerResponse(QJsonObject{{"error", error}}, Status::BadRequest);
        }
        refresh();
        return QHttpServerResponse(snapshot());
    });
    if (!_tcp.listen(QHostAddress::LocalHost, 18320) || !_http.bind(&_tcp)) {
        _statusText = tr("助手桥监听失败：18320 端口不可用");
        return;
    }
    _statusText = tr("助手桥已连接 · 只操作草稿，不上传或起飞");
    connect(&_timer, &QTimer::timeout, this, &LinghangBridge::refresh);
    _timer.start(500);
}

bool LinghangBridge::authorized(const QHttpServerRequest& request) const
{
    return request.remoteAddress().isLoopback() && !_token.isEmpty()
        && request.value("Authorization") == QByteArray("Bearer ") + _token;
}

void LinghangBridge::setController(PlanMasterController* controller)
{
    if (_controller == controller) {
        return;
    }
    _controller = controller;
    emit controllerChanged();
    refresh();
}

void LinghangBridge::setPicking(bool value)
{
    if (_picking == value) {
        return;
    }
    _picking = value;
    emit pickingChanged();
}

void LinghangBridge::selectPosition(const QGeoCoordinate& coordinate, const QString& altitude)
{
    bool ok = false;
    const double altitudeValue = altitude.toDouble(&ok);
    if (!coordinate.isValid() || !ok || !std::isfinite(altitudeValue)) {
        _statusText = tr("请选择有效坐标并填写明确的起飞点海拔");
        emit statusTextChanged();
        return;
    }
    _selected = {{"latitude", coordinate.latitude()}, {"longitude", coordinate.longitude()},
                 {"altitudeAmslM", altitudeValue}};
    ++_revision;
    setPicking(false);
    _statusText = tr("已选择实际起飞位置，等待助手界面批准后调整仿真");
    emit statusTextChanged();
}

void LinghangBridge::refresh()
{
    if (!_controller || _controller->syncInProgress()) {
        return;
    }
    const QJsonDocument document = _controller->saveToJson();
    const QByteArray hash = QCryptographicHash::hash(_instanceId + document.toJson(QJsonDocument::Compact),
                                                  QCryptographicHash::Sha256).toHex();
    if (hash != _planHash) {
        _plan = document.object();
        _planHash = hash;
        ++_revision;
    }
}

QJsonObject LinghangBridge::snapshot()
{
    return {{"connected", !_controller.isNull()}, {"revision", _revision}, {"digest", QString::fromLatin1(_planHash)},
            {"plan", _plan}, {"selectedPosition", _selected.isEmpty() ? QJsonValue::Null : QJsonValue(_selected)},
            {"observedAt", QDateTime::currentMSecsSinceEpoch()}};
}
