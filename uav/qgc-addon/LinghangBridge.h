#pragma once

#include <QtCore/QJsonObject>
#include <QtCore/QObject>
#include <QtCore/QPointer>
#include <QtCore/QTimer>
#include <QtHttpServer/QHttpServer>
#include <QtNetwork/QTcpServer>
#include <QtPositioning/QGeoCoordinate>
#include <QtQmlIntegration/QtQmlIntegration>

#include "PlanMasterController.h"

/// Local authenticated access to the live editor, never to vehicle upload or flight commands.
class LinghangBridge : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(PlanMasterController* controller READ controller WRITE setController NOTIFY controllerChanged)
    Q_PROPERTY(bool picking READ picking WRITE setPicking NOTIFY pickingChanged)
    Q_PROPERTY(QString statusText READ statusText NOTIFY statusTextChanged)

public:
    explicit LinghangBridge(QObject* parent = nullptr);
    PlanMasterController* controller() const { return _controller; }
    void setController(PlanMasterController* controller);
    bool picking() const { return _picking; }
    void setPicking(bool value);
    QString statusText() const { return _statusText; }
    Q_INVOKABLE void selectPosition(const QGeoCoordinate& coordinate, const QString& altitude);

signals:
    void controllerChanged();
    void pickingChanged();
    void statusTextChanged();

private:
    bool authorized(const QHttpServerRequest& request) const;
    QJsonObject snapshot();
    void refresh();
    QPointer<PlanMasterController> _controller;
    QHttpServer _http;
    QTcpServer _tcp;
    QTimer _timer;
    QByteArray _token;
    QByteArray _planHash;
    QByteArray _instanceId;
    QJsonObject _plan;
    QJsonObject _selected;
    qint64 _revision = 0;
    bool _picking = false;
    QString _statusText;
};
